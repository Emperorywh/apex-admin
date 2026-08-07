"""RBAC 模块应用服务 / Use Case（SPEC §5.2、§5.6、§5.7、§13）。

Use Case 编排角色管理、权限分配、用户-角色关系管理、管理范围强制和
超级管理员保护：

1. 在 ``async with`` 上下文中打开 :class:`RbacUnitOfWork`
2. 执行领域策略校验
3. 通过 Repository 端口执行数据操作
4. 关键写 Use Case 在当前 UoW 中重新读取授权关系并二次校验（SPEC §13.3）
5. 收集领域事件，在提交前通过事件调度器同步执行
6. 退出 ``async with`` 时由 UoW 统一提交（SPEC §5.6）

管理范围强制（SPEC §13.2）：
- 用户范围为全部启用角色的权限点并集
- 普通管理员只能授予自身范围内的权限和角色
- 普通管理员只能管理范围是自身子集的用户
- 超级管理员不受限制
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.errors import AuthorizationError, ConflictError, NotFoundError
from app.events.dispatcher import TransactionalEventDispatcher
from app.modules.rbac.application.port import (
    RbacApplicationPort,
    RbacUnitOfWork,
)
from app.modules.rbac.domain.events import (
    RoleCreated,
    RoleDisabled,
    UserRoleAssigned,
    UserRoleRemoved,
)
from app.modules.rbac.domain.model import Role

_logger = logging.getLogger("app.modules.rbac.service")


class RbacService(RbacApplicationPort):
    """RBAC 模块应用服务（SPEC §13.1–13.4、§23.5）。

    实现角色 CRUD、权限分配、用户-角色管理和超级管理员保护的全部 Use Case。

    管理范围通过权限点集合运算实现（SPEC §13.2）：
    - :meth:`_get_actor_scope` 查询操作者全部启用角色的权限点并集
    - :meth:`_check_grantable_permissions` 校验请求的权限是否在操作者范围内
    - :meth:`_check_target_user_manageable` 校验目标用户范围是操作者范围的子集

    超级管理员通过角色标志检测（SPEC §13.4：禁止魔法用户 ID）。

    Args:
        uow_factory: 工作单元工厂
        event_dispatcher: 事务内事件调度器
    """

    def __init__(
        self,
        uow_factory: Callable[[], RbacUnitOfWork],
        event_dispatcher: TransactionalEventDispatcher,
    ) -> None:
        self._uow_factory = uow_factory
        self._event_dispatcher = event_dispatcher

    # ------------------------------------------------------------------
    # 角色 CRUD（SPEC §13.2）
    # ------------------------------------------------------------------

    async def create_role(
        self,
        *,
        code: str,
        name: str,
        description: str | None,
        is_super_admin: bool,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """创建角色 Use Case（SPEC §13.2）。"""
        async with self._uow_factory() as uow:
            existing = await uow.roles.get_by_code(code)
            if existing is not None:
                raise ConflictError(
                    "角色编码已存在",
                    code="RBAC.ROLE_ALREADY_EXISTS",
                )

            role = Role.new(
                code=code,
                name=name,
                description=description,
                is_super_admin=is_super_admin,
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.roles.add(role)

            self._event_dispatcher.collect(
                RoleCreated(
                    occurred_at=current_time,
                    role_id=role.id,
                    role_code=role.code,
                    is_super_admin=role.is_super_admin,
                )
            )
            await self._event_dispatcher.flush(uow)

            return role

    async def get_role(self, role_id: UUID) -> Role:
        """查询角色详情 Use Case（SPEC §13.2）。"""
        async with self._uow_factory() as uow:
            role = await uow.roles.get_by_id(role_id)
            if role is None:
                raise NotFoundError(
                    "角色不存在",
                    code="RBAC.ROLE_NOT_FOUND",
                )
            return role

    async def list_roles(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[Role], int]:
        """分页查询角色列表 Use Case（SPEC §13.2）。"""
        async with self._uow_factory() as uow:
            total = await uow.roles.count()
            offset = (page - 1) * page_size
            roles = await uow.roles.list_paginated(offset, page_size)
            return roles, total

    async def update_role(
        self,
        *,
        role_id: UUID,
        field_updates: dict[str, str | None],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """更新角色 Use Case（SPEC §13.2）。"""
        async with self._uow_factory() as uow:
            role = await uow.roles.get_by_id(role_id)
            if role is None:
                raise NotFoundError(
                    "角色不存在",
                    code="RBAC.ROLE_NOT_FOUND",
                )

            updated_role = role.update(
                field_updates=field_updates,
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.roles.update(updated_role)
            return updated_role

    async def enable_role(
        self,
        *,
        role_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """启用角色 Use Case（SPEC §13.2）。"""
        async with self._uow_factory() as uow:
            role = await uow.roles.get_by_id(role_id)
            if role is None:
                raise NotFoundError(
                    "角色不存在",
                    code="RBAC.ROLE_NOT_FOUND",
                )

            enabled_role = role.enable(
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.roles.update(enabled_role)
            return enabled_role

    async def disable_role(
        self,
        *,
        role_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """禁用角色 Use Case（SPEC §13.2）。

        内置超级管理员角色不可禁用（SPEC §13.4：防止系统失去超级管理员能力）。
        """
        async with self._uow_factory() as uow:
            role = await uow.roles.get_by_id(role_id)
            if role is None:
                raise NotFoundError(
                    "角色不存在",
                    code="RBAC.ROLE_NOT_FOUND",
                )

            if role.is_builtin and role.is_super_admin:
                raise ConflictError(
                    "内置超级管理员角色不可禁用",
                    code="RBAC.BUILTIN_ROLE_PROTECTED",
                )

            disabled_role = role.disable(
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.roles.update(disabled_role)

            self._event_dispatcher.collect(
                RoleDisabled(
                    occurred_at=current_time,
                    role_id=disabled_role.id,
                    role_code=disabled_role.code,
                )
            )
            await self._event_dispatcher.flush(uow)

            return disabled_role

    # ------------------------------------------------------------------
    # 角色-权限分配（SPEC §13.2）
    # ------------------------------------------------------------------

    async def assign_permissions_to_role(
        self,
        *,
        role_id: UUID,
        permission_codes: frozenset[str],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> frozenset[str]:
        """为角色分配权限点 Use Case（SPEC §13.2、§13.3）。

        全量替换语义。关键写 Use Case 在当前 UoW 中重新读取操作者授权关系
        并执行管理范围二次校验（SPEC §13.3）。

        普通管理员只能授予自身范围内的权限（SPEC §13.2）。
        """
        async with self._uow_factory() as uow:
            role = await uow.roles.get_by_id(role_id)
            if role is None:
                raise NotFoundError(
                    "角色不存在",
                    code="RBAC.ROLE_NOT_FOUND",
                )

            # 管理范围二次校验——在当前 UoW 中重新读取（SPEC §13.3）
            if actor_id is not None:
                await self._enforce_scope_for_permission_grant(
                    uow=uow,
                    permission_codes=permission_codes,
                    actor_id=actor_id,
                )

            await uow.role_permissions.set_for_role(role_id, permission_codes)
            return permission_codes

    async def get_role_permissions(self, role_id: UUID) -> frozenset[str]:
        """查询角色的权限点编码集合。"""
        async with self._uow_factory() as uow:
            return await uow.role_permissions.get_for_role(role_id)

    # ------------------------------------------------------------------
    # 用户-角色分配（SPEC §13.2）
    # ------------------------------------------------------------------

    async def assign_roles_to_user(
        self,
        *,
        user_id: UUID,
        role_codes: frozenset[str],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> None:
        """为用户分配角色 Use Case（SPEC §13.2、§13.3）。

        增量语义。关键写 Use Case 在当前 UoW 中重新读取授权关系并二次校验
        （SPEC §13.3）。

        普通管理员只能授予自身范围内的角色，只能管理范围是自身子集的用户
        （SPEC §13.2）。
        """
        async with self._uow_factory() as uow:
            # 解析角色编码为实体
            roles_to_assign: list[Role] = []
            for code in role_codes:
                role = await uow.roles.get_by_code(code)
                if role is None:
                    raise NotFoundError(
                        f"角色不存在: {code}",
                        code="RBAC.ROLE_NOT_FOUND",
                    )
                roles_to_assign.append(role)

            # 管理范围二次校验——在当前 UoW 中重新读取（SPEC §13.3）
            if actor_id is not None:
                await self._enforce_scope_for_role_grant(
                    uow=uow,
                    roles_to_assign=roles_to_assign,
                    target_user_id=user_id,
                    actor_id=actor_id,
                )

            for role in roles_to_assign:
                await uow.user_roles.assign(
                    user_id=user_id,
                    role_id=role.id,
                    assigned_at=current_time,
                    assigned_by=actor_id,
                )
                self._event_dispatcher.collect(
                    UserRoleAssigned(
                        occurred_at=current_time,
                        user_id=user_id,
                        role_id=role.id,
                        role_code=role.code,
                    )
                )

            await self._event_dispatcher.flush(uow)

    async def remove_roles_from_user(
        self,
        *,
        user_id: UUID,
        role_codes: frozenset[str],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> None:
        """移除用户角色 Use Case（SPEC §13.2、§13.3、§13.4）。

        普通管理员只能管理范围是自身子集的用户（SPEC §13.2）。
        禁止移除导致系统失去最后一个可用超级管理员的操作（SPEC §13.4）。
        """
        async with self._uow_factory() as uow:
            # 解析角色编码为实体
            roles_to_remove: list[Role] = []
            for code in role_codes:
                role = await uow.roles.get_by_code(code)
                if role is None:
                    raise NotFoundError(
                        f"角色不存在: {code}",
                        code="RBAC.ROLE_NOT_FOUND",
                    )
                roles_to_remove.append(role)

            # 管理范围二次校验（SPEC §13.3）
            if actor_id is not None:
                await self._enforce_scope_for_target_user(
                    uow=uow,
                    target_user_id=user_id,
                    actor_id=actor_id,
                )

            # 超级管理员保护——移除后不能失去最后一个可用超级管理员（SPEC §13.4）
            await self._enforce_last_super_admin_protection(
                uow=uow,
                user_id=user_id,
                roles_to_remove=roles_to_remove,
            )

            for role in roles_to_remove:
                await uow.user_roles.remove(user_id, role.id)
                self._event_dispatcher.collect(
                    UserRoleRemoved(
                        occurred_at=current_time,
                        user_id=user_id,
                        role_id=role.id,
                        role_code=role.code,
                    )
                )

            await self._event_dispatcher.flush(uow)

    async def get_role_members(self, role_id: UUID) -> list[UUID]:
        """查询角色成员列表 Use Case（SPEC §13.2）。"""
        async with self._uow_factory() as uow:
            return await uow.user_roles.get_user_ids_for_role(role_id)

    async def get_user_roles(self, user_id: UUID) -> list[Role]:
        """查询用户的角色列表。"""
        async with self._uow_factory() as uow:
            role_ids = await uow.user_roles.get_role_ids_for_user(user_id)
            roles: list[Role] = []
            for rid in role_ids:
                role = await uow.roles.get_by_id(rid)
                if role is not None:
                    roles.append(role)
            return roles

    # ------------------------------------------------------------------
    # 权限查询（SPEC §13.3）
    # ------------------------------------------------------------------

    async def get_user_permissions(self, user_id: UUID) -> frozenset[str]:
        """查询用户全部启用角色的权限点编码并集（SPEC §13.2 管理范围）。"""
        async with self._uow_factory() as uow:
            return await uow.role_permissions.get_for_user(user_id)

    async def is_user_super_admin(self, user_id: UUID) -> bool:
        """判断用户是否拥有超级管理员角色（SPEC §13.4）。

        基于角色标志检测，不使用魔法用户 ID（SPEC §13.4）。
        """
        async with self._uow_factory() as uow:
            role_ids = await uow.user_roles.get_active_role_ids_for_user(user_id)
            for rid in role_ids:
                role = await uow.roles.get_by_id(rid)
                if role is not None and role.is_super_admin:
                    return True
            return False

    # ------------------------------------------------------------------
    # 管理范围强制——内部方法（SPEC §13.2、§13.3）
    # ------------------------------------------------------------------

    async def _enforce_scope_for_permission_grant(
        self,
        *,
        uow: RbacUnitOfWork,
        permission_codes: frozenset[str],
        actor_id: UUID,
    ) -> None:
        """校验操作者是否有权授予指定的权限点（SPEC §13.2、§13.3）。

        在当前 UoW 中重新读取操作者的权限范围（SPEC §13.3 二次校验）。
        超级管理员不受限制。
        """
        is_super = await self._is_super_admin_in_uow(uow, actor_id)
        if is_super:
            return

        actor_scope = await uow.role_permissions.get_for_user(actor_id)
        out_of_scope = permission_codes - actor_scope
        if out_of_scope:
            raise AuthorizationError(
                "权限超出管理范围",
                code="RBAC.INSUFFICIENT_SCOPE",
            )

    async def _enforce_scope_for_role_grant(
        self,
        *,
        uow: RbacUnitOfWork,
        roles_to_assign: list[Role],
        target_user_id: UUID,
        actor_id: UUID,
    ) -> None:
        """校验操作者是否有权授予指定的角色和管理目标用户（SPEC §13.2）。

        在当前 UoW 中重新读取授权关系（SPEC §13.3 二次校验）。

        检查两项：
        1. 每个待分配角色的权限集是操作者范围的子集（角色可授予）
        2. 目标用户当前范围是操作者范围的子集（用户可管理）

        超级管理员不受限制。
        """
        is_super = await self._is_super_admin_in_uow(uow, actor_id)
        if is_super:
            return

        actor_scope = await uow.role_permissions.get_for_user(actor_id)

        # 检查角色可授予——角色权限集是操作者范围的子集
        for role in roles_to_assign:
            role_permissions = await uow.role_permissions.get_for_role(role.id)
            out_of_scope = role_permissions - actor_scope
            if out_of_scope:
                raise AuthorizationError(
                    "角色权限超出管理范围",
                    code="RBAC.INSUFFICIENT_SCOPE",
                )

        # 检查目标用户可管理——目标用户范围是操作者范围的子集
        await self._check_target_subset(uow, target_user_id, actor_scope)

    async def _enforce_scope_for_target_user(
        self,
        *,
        uow: RbacUnitOfWork,
        target_user_id: UUID,
        actor_id: UUID,
    ) -> None:
        """校验操作者是否有权管理目标用户（SPEC §13.2）。

        目标用户的管理范围必须是操作者管理范围的子集。
        超级管理员不受限制。
        """
        is_super = await self._is_super_admin_in_uow(uow, actor_id)
        if is_super:
            return

        actor_scope = await uow.role_permissions.get_for_user(actor_id)
        await self._check_target_subset(uow, target_user_id, actor_scope)

    async def _check_target_subset(
        self,
        uow: RbacUnitOfWork,
        target_user_id: UUID,
        actor_scope: frozenset[str],
    ) -> None:
        """检查目标用户范围是操作者范围的子集（SPEC §13.2）。"""
        target_scope = await uow.role_permissions.get_for_user(target_user_id)
        out_of_scope = target_scope - actor_scope
        if out_of_scope:
            raise AuthorizationError(
                "目标用户管理范围超出操作者范围",
                code="RBAC.INSUFFICIENT_SCOPE",
            )

    async def _is_super_admin_in_uow(
        self,
        uow: RbacUnitOfWork,
        user_id: UUID,
    ) -> bool:
        """在当前 UoW 中判断用户是否为超级管理员（SPEC §13.4）。

        基于角色标志检测，不使用魔法用户 ID。
        """
        role_ids = await uow.user_roles.get_active_role_ids_for_user(user_id)
        for rid in role_ids:
            role = await uow.roles.get_by_id(rid)
            if role is not None and role.is_super_admin:
                return True
        return False

    async def _enforce_last_super_admin_protection(
        self,
        *,
        uow: RbacUnitOfWork,
        user_id: UUID,
        roles_to_remove: list[Role],
    ) -> None:
        """超级管理员保护——防止系统失去最后一个可用超级管理员（SPEC §13.4）。

        如果移除操作会导致系统没有可用的超级管理员用户，则拒绝操作。

        "可用"指用户处于启用状态且拥有至少一个启用的超级管理员角色。
        """
        # 判断是否在移除超级管理员角色
        removing_super_admin = any(r.is_super_admin for r in roles_to_remove)
        if not removing_super_admin:
            return

        # 查询当前拥有超级管理员角色的全部用户
        super_admin_user_ids = await uow.user_roles.get_super_admin_user_ids()

        # 模拟移除后的状态：如果目标用户的所有超级管理员角色都被移除，
        # 且没有其他用户拥有超级管理员角色，则拒绝
        target_role_ids = await uow.user_roles.get_role_ids_for_user(user_id)
        removing_role_ids = {r.id for r in roles_to_remove}

        # 目标用户移除后是否仍有超级管理员角色
        all_target_role_ids = set(target_role_ids) - removing_role_ids
        target_still_super = False
        for rid in all_target_role_ids:
            role = await uow.roles.get_by_id(rid)
            if role is not None and role.is_super_admin and role.is_active:
                target_still_super = True
                break

        if not target_still_super:
            # 目标用户将失去超级管理员角色
            # 检查是否还有其他用户拥有超级管理员角色
            other_super_admins = [uid for uid in super_admin_user_ids if uid != user_id]
            if len(other_super_admins) == 0:
                raise ConflictError(
                    "不能移除系统最后一个可用超级管理员",
                    code="RBAC.LAST_SUPER_ADMIN",
                )

    # ------------------------------------------------------------------
    # 超级管理员关键操作审计日志（SPEC §13.4）
    # ------------------------------------------------------------------

    def _audit_super_admin_operation(
        self,
        *,
        operation: str,
        actor_id: UUID | None,
        target_user_id: UUID | None = None,
        **extra: str,
    ) -> None:
        """记录超级管理员关键操作审计日志（SPEC §13.4）。

        审计日志持久化（G3）由审计模块独立实现。此处记录结构化日志
        供审计追踪。
        """
        log_extra: dict[str, str] = {
            "event": "super_admin_operation",
            "operation": operation,
            "actor_id": str(actor_id) if actor_id else "",
        }
        if target_user_id is not None:
            log_extra["target_user_id"] = str(target_user_id)
        log_extra.update(extra)
        _logger.warning("超级管理员关键操作", extra=log_extra)
