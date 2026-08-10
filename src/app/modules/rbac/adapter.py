"""RBAC Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``RbacRepository`` 和 ``UserRbacPort``。
Adapter 在内部将 ORM 模型与领域实体互转，确保内层不感知 ORM 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError

from app.core.api.pagination import SortField, SortOrder
from app.core.errors.exceptions import UniqueViolationError
from app.infrastructure.db.exceptions import translate_db_exception
from app.modules.rbac.errors import (
    RoleAlreadyExistsError,
    UserRoleAlreadyAssignedError,
)
from app.modules.rbac.models import (
    Permission,
    Role,
    RoleAssignment,
    RoleStatus,
)
from app.modules.rbac.orm import (
    PermissionORM,
    RoleORM,
    RolePermissionORM,
    UserRoleORM,
)
from app.modules.rbac.port import RbacRepository, UserRbacPort

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyRbacRepository(RbacRepository):
    """SQLAlchemy 异步 RBAC Repository Adapter — 实现 ``RbacRepository`` Port."""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    # ── 角色 ────────────────────────────────────────────────────────────

    async def add_role(self, role: Role) -> None:
        """添加新角色到当前事务."""

        orm = _role_to_orm(role)
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                raise RoleAlreadyExistsError(
                    f"角色编码 '{role.code}' 已存在",
                ) from exc
            raise

    async def get_role_by_id(self, role_id: UUID) -> Role | None:
        """按 ID 查询角色."""

        stmt = select(RoleORM).where(RoleORM.id == role_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_role(orm) if orm else None

    async def get_roles_by_codes(self, codes: set[str]) -> list[Role]:
        """按编码集合查询角色."""

        if not codes:
            return []
        stmt = select(RoleORM).where(RoleORM.code.in_(codes))
        result = await self._session.execute(stmt)
        return [_orm_to_role(orm) for orm in result.scalars().all()]

    async def get_roles_by_ids(self, ids: set[UUID]) -> list[Role]:
        """按 ID 集合查询角色."""

        if not ids:
            return []
        stmt = select(RoleORM).where(RoleORM.id.in_(ids))
        result = await self._session.execute(stmt)
        return [_orm_to_role(orm) for orm in result.scalars().all()]

    async def list_roles(
        self,
        *,
        offset: int,
        limit: int,
        sort_fields: list[SortField],
        status_filter: RoleStatus | None,
    ) -> tuple[list[Role], int]:
        """分页查询角色列表."""

        count_stmt = select(func.count()).select_from(RoleORM)
        if status_filter is not None:
            count_stmt = count_stmt.where(RoleORM.status == status_filter.value)
        total = (await self._session.execute(count_stmt)).scalar() or 0

        stmt = select(RoleORM)
        if status_filter is not None:
            stmt = stmt.where(RoleORM.status == status_filter.value)
        stmt = stmt.offset(offset).limit(limit)
        for sf in sort_fields:
            col = getattr(RoleORM, sf.name)
            stmt = stmt.order_by(
                col.desc() if sf.order == SortOrder.DESC else col.asc(),
            )
        result = await self._session.execute(stmt)
        roles = [_orm_to_role(orm) for orm in result.scalars().all()]
        return roles, int(total)

    async def save_role(self, role: Role) -> None:
        """保存角色变更."""

        stmt = select(RoleORM).where(RoleORM.id == role.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.rbac.errors import RoleNotFoundError

            raise RoleNotFoundError(str(role.id))

        orm.code = role.code
        orm.display_name = role.display_name
        orm.description = role.description
        orm.status = role.status.value
        orm.is_builtin = role.is_builtin
        orm.sort_order = role.sort_order
        orm.updated_at = role.updated_at
        orm.updated_by = role.updated_by
        await self._session.flush()

    async def delete_role_by_id(self, role_id: UUID) -> bool:
        """按 ID 物理删除角色."""

        stmt = select(RoleORM).where(RoleORM.id == role_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    # ── 权限点 ──────────────────────────────────────────────────────────

    async def get_permission_codes(self, codes: set[str]) -> list[Permission]:
        """按编码集合查询权限点."""

        if not codes:
            return []
        stmt = select(PermissionORM).where(PermissionORM.code.in_(codes))
        result = await self._session.execute(stmt)
        return [_orm_to_permission(orm) for orm in result.scalars().all()]

    async def add_permission(self, permission: Permission) -> None:
        """添加新权限点."""

        orm = _permission_to_orm(permission)
        self._session.add(orm)
        await self._session.flush()

    async def update_permission(self, permission: Permission) -> None:
        """更新权限点."""

        stmt = select(PermissionORM).where(PermissionORM.id == permission.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return
        orm.display_name = permission.display_name
        orm.description = permission.description
        orm.module_code = permission.module_code
        orm.is_active = permission.is_active
        orm.updated_at = permission.updated_at
        await self._session.flush()

    async def list_all_permissions(self) -> list[Permission]:
        """查询全部权限点."""

        stmt = select(PermissionORM).order_by(PermissionORM.code)
        result = await self._session.execute(stmt)
        return [_orm_to_permission(orm) for orm in result.scalars().all()]

    async def delete_permissions_by_ids(self, ids: set[UUID]) -> int:
        """按 ID 集合删除权限点."""

        if not ids:
            return 0
        stmt = delete(PermissionORM).where(PermissionORM.id.in_(ids))
        result = await self._session.execute(stmt)
        await self._session.flush()
        # CursorResult.rowcount 在运行时可用，但 Result 类型注解未暴露。
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return int(rowcount)

    # ── 角色-权限点 ────────────────────────────────────────────────────

    async def replace_role_permissions(
        self,
        role_id: UUID,
        permission_ids: set[UUID],
        *,
        now: object,
    ) -> None:
        """替换角色的全部权限点 — 全量覆盖."""

        from datetime import datetime

        assert isinstance(now, datetime)

        # 删除现有全部关联
        await self._session.execute(
            delete(RolePermissionORM).where(RolePermissionORM.role_id == role_id),
        )

        # 插入新关联
        for pid in permission_ids:
            await self._session.execute(
                insert(RolePermissionORM).values(
                    role_id=role_id,
                    permission_id=pid,
                    created_at=now,
                ),
            )
        await self._session.flush()

    async def get_role_permission_codes(self, role_id: UUID) -> list[str]:
        """查询角色已分配的权限编码列表."""

        stmt = (
            select(PermissionORM.code)
            .join(
                RolePermissionORM,
                RolePermissionORM.permission_id == PermissionORM.id,
            )
            .where(RolePermissionORM.role_id == role_id)
            .order_by(PermissionORM.code)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── 用户-角色 ──────────────────────────────────────────────────────

    async def add_user_role(
        self,
        user_id: UUID,
        role_id: UUID,
        *,
        now: object,
        created_by: str | None,
    ) -> None:
        """添加用户角色关系."""

        from datetime import datetime

        assert isinstance(now, datetime)

        orm = UserRoleORM(
            user_id=user_id,
            role_id=role_id,
            created_at=now,
            created_by=created_by,
        )
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                raise UserRoleAlreadyAssignedError(
                    f"用户 {user_id} 已分配角色 {role_id}",
                ) from exc
            raise

    async def remove_user_role(self, user_id: UUID, role_id: UUID) -> bool:
        """移除用户角色关系."""

        stmt = select(UserRoleORM).where(
            UserRoleORM.user_id == user_id,
            UserRoleORM.role_id == role_id,
        )
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    async def list_user_roles(self, user_id: UUID) -> list[RoleAssignment]:
        """查询用户的全部角色分配记录."""

        stmt = select(UserRoleORM).where(UserRoleORM.user_id == user_id)
        result = await self._session.execute(stmt)
        return [_orm_to_role_assignment(orm) for orm in result.scalars().all()]

    async def list_role_members(
        self,
        role_id: UUID,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[RoleAssignment], int]:
        """分页查询角色成员."""

        count_stmt = (
            select(func.count())
            .select_from(UserRoleORM)
            .where(UserRoleORM.role_id == role_id)
        )
        total = (await self._session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(UserRoleORM)
            .where(UserRoleORM.role_id == role_id)
            .offset(offset)
            .limit(limit)
            .order_by(UserRoleORM.created_at)
        )
        result = await self._session.execute(stmt)
        members = [_orm_to_role_assignment(orm) for orm in result.scalars().all()]
        return members, int(total)

    async def count_role_members(self, role_id: UUID) -> int:
        """查询角色成员数量."""

        stmt = (
            select(func.count())
            .select_from(UserRoleORM)
            .where(UserRoleORM.role_id == role_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def count_roles_for_user(self, user_id: UUID) -> int:
        """查询用户分配的角色数量."""

        stmt = (
            select(func.count())
            .select_from(UserRoleORM)
            .where(UserRoleORM.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)


class SqlAlchemyUserRbacAdapter(UserRbacPort):
    """SQLAlchemy 用户 RBAC 信息 Adapter — 实现 ``UserRbacPort`` Port.

    SPEC 5.5: auth 模块（TASK-016）通过此 Port 跨模块查询用户有效权限。
    SPEC 13.3: 每次调用查库，无 TTL 缓存。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def get_effective_permission_codes(self, user_id: UUID) -> set[str]:
        """查询用户有效权限编码集合 — 每次查库，无缓存（SPEC 13.3）.

        有效权限 = 用户全部启用角色的权限点编码并集。
        被禁用角色的权限不计入（SPEC 13.1 / 13.2）。

        查询链:
            rbac_user_roles → rbac_roles (status=active)
            → rbac_role_permissions → rbac_permissions (is_active=true)
        """

        stmt = (
            select(PermissionORM.code)
            .join(
                RolePermissionORM,
                RolePermissionORM.permission_id == PermissionORM.id,
            )
            .join(
                RoleORM,
                RoleORM.id == RolePermissionORM.role_id,
            )
            .join(
                UserRoleORM,
                UserRoleORM.role_id == RoleORM.id,
            )
            .where(
                UserRoleORM.user_id == user_id,
                RoleORM.status == RoleStatus.ACTIVE.value,
                PermissionORM.is_active.is_(True),
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def get_role_ids_by_user(self, user_id: UUID) -> list[UUID]:
        """查询用户全部角色 ID 列表."""

        stmt = select(UserRoleORM.role_id).where(UserRoleORM.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_role_codes_by_user(self, user_id: UUID) -> set[str]:
        """查询用户全部角色编码集合 — SPEC 13.4.

        用于超管判定（检查是否拥有 ``super_admin`` 角色编码）。
        """

        stmt = (
            select(RoleORM.code)
            .join(UserRoleORM, UserRoleORM.role_id == RoleORM.id)
            .where(UserRoleORM.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def get_user_ids_by_role_code(self, role_code: str) -> set[UUID]:
        """查询拥有指定角色编码的全部用户 ID — SPEC 13.4."""

        stmt = (
            select(UserRoleORM.user_id)
            .join(RoleORM, RoleORM.id == UserRoleORM.role_id)
            .where(RoleORM.code == role_code)
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _orm_to_role(orm: RoleORM) -> Role:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return Role(
        id=orm.id,
        code=orm.code,
        display_name=orm.display_name,
        description=orm.description,
        status=RoleStatus(orm.status),
        is_builtin=orm.is_builtin,
        sort_order=orm.sort_order,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _role_to_orm(role: Role) -> RoleORM:
    """领域实体 → ORM 模型转换."""

    return RoleORM(
        id=role.id,
        code=role.code,
        display_name=role.display_name,
        description=role.description,
        status=role.status.value,
        is_builtin=role.is_builtin,
        sort_order=role.sort_order,
        created_at=role.created_at,
        updated_at=role.updated_at,
        created_by=role.created_by,
        updated_by=role.updated_by,
    )


def _orm_to_permission(orm: PermissionORM) -> Permission:
    """ORM 模型 → 领域实体转换."""

    return Permission(
        id=orm.id,
        code=orm.code,
        display_name=orm.display_name,
        description=orm.description,
        module_code=orm.module_code,
        is_active=orm.is_active,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def _permission_to_orm(permission: Permission) -> PermissionORM:
    """领域实体 → ORM 模型转换."""

    return PermissionORM(
        id=permission.id,
        code=permission.code,
        display_name=permission.display_name,
        description=permission.description,
        module_code=permission.module_code,
        is_active=permission.is_active,
        created_at=permission.created_at,
        updated_at=permission.updated_at,
    )


def _orm_to_role_assignment(orm: UserRoleORM) -> RoleAssignment:
    """ORM 模型 → 领域实体转换."""

    return RoleAssignment(
        user_id=orm.user_id,
        role_id=orm.role_id,
        created_at=orm.created_at,
        created_by=orm.created_by,
    )
