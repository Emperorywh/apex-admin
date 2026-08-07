"""RBAC 模块 Repository Adapter（SPEC §5.2）。

实现 :class:`~app.modules.rbac.application.port` 中的三个 Repository 端口，
使用 SQLAlchemy AsyncSession 执行数据访问。Repository 不自行提交或回滚，
所有操作在传入的 Session（由 UoW 管理）的事务作用域内执行（SPEC §5.6）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.application.port import (
    RolePermissionRepository,
    RoleRepository,
    UserRoleRepository,
)
from app.modules.rbac.domain.model import Role
from app.modules.rbac.infrastructure.models import (
    RoleModel,
    RolePermissionModel,
    UserRoleModel,
)


class SqlAlchemyRoleRepository(RoleRepository):
    """基于 SQLAlchemy 的角色 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: Role) -> None:
        model = RoleModel.from_entity(entity)
        self._session.add(model)

    async def get_by_id(self, role_id: UUID) -> Role | None:
        stmt = select(RoleModel).where(RoleModel.id == role_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def get_by_code(self, code: str) -> Role | None:
        stmt = select(RoleModel).where(RoleModel.code == code)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def count(self) -> int:
        stmt = select(func.count()).select_from(RoleModel)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_paginated(
        self,
        offset: int,
        limit: int,
    ) -> list[Role]:
        stmt = select(RoleModel).order_by(RoleModel.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]

    async def update(self, entity: Role) -> None:
        model = RoleModel.from_entity(entity)
        await self._session.merge(model)

    async def list_all(self) -> list[Role]:
        stmt = select(RoleModel)
        result = await self._session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]


class SqlAlchemyUserRoleRepository(UserRoleRepository):
    """基于 SQLAlchemy 的用户-角色关系 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assign(
        self,
        *,
        user_id: UUID,
        role_id: UUID,
        assigned_at: datetime,
        assigned_by: UUID | None = None,
    ) -> None:
        # 幂等：已存在则跳过
        existing = await self._session.execute(
            select(UserRoleModel).where(
                UserRoleModel.user_id == user_id,
                UserRoleModel.role_id == role_id,
            ),
        )
        if existing.scalar_one_or_none() is not None:
            return

        self._session.add(
            UserRoleModel(
                user_id=user_id,
                role_id=role_id,
                assigned_at=assigned_at,
                assigned_by=assigned_by,
            )
        )

    async def remove(self, user_id: UUID, role_id: UUID) -> None:
        await self._session.execute(
            delete(UserRoleModel).where(
                UserRoleModel.user_id == user_id,
                UserRoleModel.role_id == role_id,
            ),
        )

    async def get_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        stmt = select(UserRoleModel.role_id).where(UserRoleModel.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        """查询用户的启用角色 ID。

        JOIN roles 表过滤 status='active'。
        """
        stmt = (
            select(UserRoleModel.role_id)
            .join(RoleModel, UserRoleModel.role_id == RoleModel.id)
            .where(
                UserRoleModel.user_id == user_id,
                RoleModel.status == "active",
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_user_ids_for_role(self, role_id: UUID) -> list[UUID]:
        stmt = select(UserRoleModel.user_id).where(UserRoleModel.role_id == role_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_super_admin_user_ids(self) -> list[UUID]:
        """查询拥有超级管理员角色的全部用户 ID。

        JOIN roles 表过滤 is_super_admin=true 且 status='active'。
        """
        stmt = (
            select(UserRoleModel.user_id)
            .join(RoleModel, UserRoleModel.role_id == RoleModel.id)
            .where(
                RoleModel.is_super_admin.is_(True),
                RoleModel.status == "active",
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class SqlAlchemyRolePermissionRepository(RolePermissionRepository):
    """基于 SQLAlchemy 的角色-权限关系 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def set_for_role(
        self,
        role_id: UUID,
        permission_codes: frozenset[str],
    ) -> None:
        """全量替换角色的权限点集合。

        删除现有权限后插入新权限集合。
        """
        await self._session.execute(
            delete(RolePermissionModel).where(
                RolePermissionModel.role_id == role_id,
            ),
        )
        for code in permission_codes:
            await self._session.execute(
                insert(RolePermissionModel).values(
                    role_id=role_id,
                    permission_code=code,
                ),
            )

    async def get_for_role(self, role_id: UUID) -> frozenset[str]:
        stmt = select(RolePermissionModel.permission_code).where(
            RolePermissionModel.role_id == role_id,
        )
        result = await self._session.execute(stmt)
        return frozenset(result.scalars().all())

    async def get_for_user(self, user_id: UUID) -> frozenset[str]:
        """查询用户全部启用角色的权限点编码并集。

        JOIN user_roles → roles (status='active') → role_permissions，
        取 permission_code 的 DISTINCT 并集（SPEC §13.2 管理范围）。
        """
        stmt = (
            select(RolePermissionModel.permission_code)
            .join(RoleModel, RolePermissionModel.role_id == RoleModel.id)
            .join(UserRoleModel, RolePermissionModel.role_id == UserRoleModel.role_id)
            .where(
                UserRoleModel.user_id == user_id,
                RoleModel.status == "active",
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return frozenset(result.scalars().all())
