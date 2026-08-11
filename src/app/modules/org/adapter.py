"""组织模块 Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``OrgRepository``。
Adapter 在内部将 ORM 模型与领域实体互转，确保内层不感知 ORM 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.errors.exceptions import UniqueViolationError
from app.infrastructure.db.exceptions import translate_db_exception
from app.modules.org.errors import DepartmentAlreadyExistsError
from app.modules.org.models import Department, DepartmentStatus
from app.modules.org.orm import DepartmentORM
from app.modules.org.port import OrgRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

#: PostgreSQL 事务级咨询锁的键 — 序列化并发层级调整（SPEC 14.1）。
#: 固定整数键，确保所有层级调整事务竞争同一把锁。
#: 锁在事务提交或回滚时自动释放（``pg_advisory_xact_lock``）。
_ADVISORY_LOCK_KEY = 40_014_001


class SqlAlchemyOrgRepository(OrgRepository):
    """SQLAlchemy 异步组织 Repository Adapter — 实现 ``OrgRepository`` Port."""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    # ── 部门 CRUD ───────────────────────────────────────────────────────

    async def add_department(self, department: Department) -> None:
        """添加新部门到当前事务."""

        orm = _department_to_orm(department)
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                raise DepartmentAlreadyExistsError(
                    f"部门编码 '{department.code}' 已存在",
                ) from exc
            raise

    async def get_department_by_id(self, department_id: UUID) -> Department | None:
        """按 ID 查询部门."""

        stmt = select(DepartmentORM).where(DepartmentORM.id == department_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_department(orm) if orm else None

    async def get_department_by_code(self, code: str) -> Department | None:
        """按编码查询部门."""

        stmt = select(DepartmentORM).where(DepartmentORM.code == code)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_department(orm) if orm else None

    async def save_department(self, department: Department) -> None:
        """保存部门变更."""

        stmt = select(DepartmentORM).where(DepartmentORM.id == department.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.org.errors import DepartmentNotFoundError

            raise DepartmentNotFoundError(str(department.id))

        orm.code = department.code
        orm.display_name = department.display_name
        orm.description = department.description
        orm.parent_id = department.parent_id
        orm.status = department.status.value
        orm.sort_order = department.sort_order
        orm.leader_id = department.leader_id
        orm.updated_at = department.updated_at
        orm.updated_by = department.updated_by
        await self._session.flush()

    async def delete_department_by_id(self, department_id: UUID) -> bool:
        """按 ID 物理删除部门."""

        stmt = select(DepartmentORM).where(DepartmentORM.id == department_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    async def list_all_departments(
        self,
        *,
        include_disabled: bool = True,
    ) -> list[Department]:
        """查询全部部门."""

        stmt = select(DepartmentORM).order_by(
            DepartmentORM.sort_order,
            DepartmentORM.display_name,
        )
        if not include_disabled:
            stmt = stmt.where(DepartmentORM.status == DepartmentStatus.ACTIVE.value)
        result = await self._session.execute(stmt)
        return [_orm_to_department(orm) for orm in result.scalars().all()]

    # ── 循环防护 ────────────────────────────────────────────────────────

    async def get_descendant_ids(self, department_id: UUID) -> set[UUID]:
        """查询部门的全部后代 ID（递归）.

        使用 PostgreSQL 递归 CTE 遍历子部门树。
        """

        recursive_sql = text(
            """
            WITH RECURSIVE descendants AS (
                SELECT id FROM org_departments WHERE parent_id = :root_id
                UNION ALL
                SELECT d.id FROM org_departments d
                INNER JOIN descendants dc ON d.parent_id = dc.id
            )
            SELECT id FROM descendants
            """,
        )
        result = await self._session.execute(recursive_sql, {"root_id": department_id})
        return {row[0] for row in result.fetchall()}

    async def acquire_hierarchy_lock(self) -> None:
        """获取事务级咨询锁 — 序列化并发层级调整."""

        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        )

    # ── 删除保护 ────────────────────────────────────────────────────────

    async def count_children(self, department_id: UUID) -> int:
        """查询部门的直接子部门数量."""

        stmt = (
            select(func.count())
            .select_from(DepartmentORM)
            .where(DepartmentORM.parent_id == department_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def count_users_in_department(self, department_id: UUID) -> int:
        """查询部门关联的用户数量.

        用户组织关系在 TASK-020 实现。当前无用户-部门关系表，返回 0。
        当 TASK-020 添加 ``org_user_departments`` 表后，此方法将查询实际关联。
        """

        return 0


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _orm_to_department(orm: DepartmentORM) -> Department:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return Department(
        id=orm.id,
        code=orm.code,
        display_name=orm.display_name,
        description=orm.description,
        parent_id=orm.parent_id,
        status=DepartmentStatus(orm.status),
        sort_order=orm.sort_order,
        leader_id=orm.leader_id,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _department_to_orm(department: Department) -> DepartmentORM:
    """领域实体 → ORM 模型转换."""

    return DepartmentORM(
        id=department.id,
        code=department.code,
        display_name=department.display_name,
        description=department.description,
        parent_id=department.parent_id,
        status=department.status.value,
        sort_order=department.sort_order,
        leader_id=department.leader_id,
        created_at=department.created_at,
        updated_at=department.updated_at,
        created_by=department.created_by,
        updated_by=department.updated_by,
    )
