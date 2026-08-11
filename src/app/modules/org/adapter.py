"""组织模块 Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``OrgRepository`` 和 ``UserOrgPort``。
Adapter 在内部将 ORM 模型与领域实体互转，确保内层不感知 ORM 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors.exceptions import UniqueViolationError
from app.infrastructure.db.exceptions import translate_db_exception
from app.modules.org.errors import (
    DepartmentAlreadyExistsError,
    DepartmentNotFoundError,
    PostAlreadyExistsError,
    PostNotFoundError,
)
from app.modules.org.models import (
    Department,
    DepartmentStatus,
    Post,
    PostStatus,
    UserDepartmentInfo,
    UserPostInfo,
)
from app.modules.org.orm import (
    DepartmentORM,
    PostORM,
    UserDepartmentORM,
    UserPostORM,
)
from app.modules.org.port import OrgRepository, UserOrgPort

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

#: PostgreSQL 事务级咨询锁的键 — 序列化并发层级调整（SPEC 14.1）。
#: 固定整数键，确保所有层级调整事务竞争同一把锁。
#: 锁在事务提交或回滚时自动释放（``pg_advisory_xact_lock``）。
_ADVISORY_LOCK_KEY = 40_014_001


class SqlAlchemyOrgRepository(OrgRepository, UserOrgPort):
    """SQLAlchemy 异步组织 Repository Adapter — 实现 ``OrgRepository``
    与 ``UserOrgPort`` Port.
    """

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

        from sqlalchemy import text

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

        from sqlalchemy import text

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
        """查询部门关联的用户数量."""

        stmt = (
            select(func.count())
            .select_from(UserDepartmentORM)
            .where(UserDepartmentORM.department_id == department_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    # ── 岗位 CRUD — SPEC 14.2 ───────────────────────────────────────────

    async def add_post(self, post: Post) -> None:
        """添加新岗位到当前事务."""

        orm = _post_to_orm(post)
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                raise PostAlreadyExistsError(
                    f"岗位编码 '{post.code}' 已存在",
                ) from exc
            raise

    async def get_post_by_id(self, post_id: UUID) -> Post | None:
        """按 ID 查询岗位."""

        stmt = select(PostORM).where(PostORM.id == post_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_post(orm) if orm else None

    async def get_post_by_code(self, code: str) -> Post | None:
        """按编码查询岗位."""

        stmt = select(PostORM).where(PostORM.code == code)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_post(orm) if orm else None

    async def save_post(self, post: Post) -> None:
        """保存岗位变更."""

        stmt = select(PostORM).where(PostORM.id == post.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            raise PostNotFoundError(str(post.id))

        orm.code = post.code
        orm.display_name = post.display_name
        orm.description = post.description
        orm.status = post.status.value
        orm.sort_order = post.sort_order
        orm.updated_at = post.updated_at
        orm.updated_by = post.updated_by
        await self._session.flush()

    async def delete_post_by_id(self, post_id: UUID) -> bool:
        """按 ID 物理删除岗位."""

        stmt = select(PostORM).where(PostORM.id == post_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    async def list_posts(
        self,
        *,
        include_disabled: bool = True,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[Post], int]:
        """查询岗位列表（分页）."""

        count_stmt = select(func.count()).select_from(PostORM)
        if not include_disabled:
            count_stmt = count_stmt.where(PostORM.status == PostStatus.ACTIVE.value)
        total = (await self._session.execute(count_stmt)).scalar() or 0

        stmt = select(PostORM).order_by(
            PostORM.sort_order,
            PostORM.display_name,
        )
        if not include_disabled:
            stmt = stmt.where(PostORM.status == PostStatus.ACTIVE.value)
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        posts = [_orm_to_post(orm) for orm in result.scalars().all()]
        return posts, int(total)

    async def count_users_for_post(self, post_id: UUID) -> int:
        """查询岗位关联的用户数量."""

        stmt = (
            select(func.count())
            .select_from(UserPostORM)
            .where(UserPostORM.post_id == post_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    # ── 用户组织关系 — SPEC 14.2 / 14.3 ─────────────────────────────────

    async def set_user_department(
        self,
        user_id: UUID,
        department_id: UUID,
        *,
        created_by: str | None,
        created_at: datetime,
    ) -> None:
        """设置用户主部门.

        SPEC 14.3: 基座默认仅主部门。
        数据库唯一约束 (user_id) 保证一个用户仅一个主部门。
        如果违反唯一约束，翻译为 ``UserAlreadyHasDepartmentError``。
        """

        from uuid import uuid4

        orm = UserDepartmentORM(
            id=uuid4(),
            user_id=user_id,
            department_id=department_id,
            is_primary=True,
            created_at=created_at,
            created_by=created_by,
        )
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                from app.modules.org.errors import UserAlreadyHasDepartmentError

                raise UserAlreadyHasDepartmentError(str(user_id)) from exc
            raise

    async def get_user_department(self, user_id: UUID) -> UserDepartmentInfo | None:
        """查询用户的主部门关系投影."""

        stmt = (
            select(
                UserDepartmentORM.department_id,
                UserDepartmentORM.is_primary,
                DepartmentORM.code,
                DepartmentORM.display_name,
            )
            .join(
                DepartmentORM,
                UserDepartmentORM.department_id == DepartmentORM.id,
            )
            .where(UserDepartmentORM.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return UserDepartmentInfo(
            department_id=row[0],
            department_code=row[2],
            department_name=row[3],
            is_primary=row[1],
        )

    async def remove_user_department(self, user_id: UUID) -> bool:
        """移除用户的主部门关系."""

        existing = await self.get_user_department(user_id)
        if existing is None:
            return False
        await self._session.execute(
            delete(UserDepartmentORM).where(
                UserDepartmentORM.user_id == user_id,
            ),
        )
        await self._session.flush()
        return True

    async def assign_user_post(
        self,
        user_id: UUID,
        post_id: UUID,
        *,
        created_by: str | None,
        created_at: datetime,
    ) -> bool:
        """为用户分配岗位 — 幂等.

        SPEC 14.2: "为用户分配岗位"。
        唯一约束 (user_id, post_id) 保证幂等——已存在时返回 False。
        """

        from uuid import uuid4

        existing_stmt = select(UserPostORM).where(
            UserPostORM.user_id == user_id,
            UserPostORM.post_id == post_id,
        )
        existing_result = await self._session.execute(existing_stmt)
        if existing_result.scalar_one_or_none() is not None:
            return False

        orm = UserPostORM(
            id=uuid4(),
            user_id=user_id,
            post_id=post_id,
            created_at=created_at,
            created_by=created_by,
        )
        self._session.add(orm)
        await self._session.flush()
        return True

    async def remove_user_post(self, user_id: UUID, post_id: UUID) -> bool:
        """移除用户岗位."""

        check_stmt = (
            select(func.count())
            .select_from(UserPostORM)
            .where(
                UserPostORM.user_id == user_id,
                UserPostORM.post_id == post_id,
            )
        )
        count_result = await self._session.execute(check_stmt)
        if int(count_result.scalar() or 0) == 0:
            return False
        await self._session.execute(
            delete(UserPostORM).where(
                UserPostORM.user_id == user_id,
                UserPostORM.post_id == post_id,
            ),
        )
        await self._session.flush()
        return True

    async def list_user_posts(self, user_id: UUID) -> list[UserPostInfo]:
        """查询用户的全部岗位关系投影."""

        stmt = (
            select(
                UserPostORM.post_id,
                PostORM.code,
                PostORM.display_name,
            )
            .join(
                PostORM,
                UserPostORM.post_id == PostORM.id,
            )
            .where(UserPostORM.user_id == user_id)
            .order_by(PostORM.sort_order, PostORM.display_name)
        )
        result = await self._session.execute(stmt)
        return [
            UserPostInfo(
                post_id=row[0],
                post_code=row[1],
                post_name=row[2],
            )
            for row in result.fetchall()
        ]

    async def clear_user_org_relations(self, user_id: UUID) -> None:
        """清除用户全部组织关系（主部门 + 岗位）.

        SPEC 14.3: "用户离职或禁用时组织关系按规则处理"。
        """

        await self._session.execute(
            delete(UserDepartmentORM).where(
                UserDepartmentORM.user_id == user_id,
            ),
        )
        await self._session.execute(
            delete(UserPostORM).where(UserPostORM.user_id == user_id),
        )
        await self._session.flush()


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


def _orm_to_post(orm: PostORM) -> Post:
    """ORM 模型 → 领域实体转换."""

    return Post(
        id=orm.id,
        code=orm.code,
        display_name=orm.display_name,
        description=orm.description,
        status=PostStatus(orm.status),
        sort_order=orm.sort_order,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _post_to_orm(post: Post) -> PostORM:
    """领域实体 → ORM 模型转换."""

    return PostORM(
        id=post.id,
        code=post.code,
        display_name=post.display_name,
        description=post.description,
        status=post.status.value,
        sort_order=post.sort_order,
        created_at=post.created_at,
        updated_at=post.updated_at,
        created_by=post.created_by,
        updated_by=post.updated_by,
    )
