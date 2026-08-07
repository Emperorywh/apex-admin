"""用户模块应用服务 / Use Case（SPEC §5.2、§5.6、§5.7、§11.1）。

Use Case 编排领域策略、密码哈希、持久化、超级管理员保护、事件发布和审计：

1. 在 ``async with`` 上下文中打开 :class:`UserUnitOfWork`
2. 调用领域策略校验业务规则
3. 通过 :class:`~app.modules.user.domain.password.PasswordHasher` 哈希密码
4. 通过 Repository 端口执行数据操作
5. 禁用用户前通过 :class:`LastSuperAdminCheck` 端口检查保护规则
6. 收集领域事件，在提交前通过事件调度器同步执行
7. 通过 :class:`~app.ports.audit.AuditPort` 显式记录操作审计（SPEC §5.7）
8. 退出 ``async with`` 时由 UoW 统一提交（SPEC §5.6）

成功操作的审计记录与业务数据在同一事务提交（SPEC §5.7、§18.2）。
审计差异使用字段白名单生成，密码等敏感字段永不进入差异（SPEC §18.2）。

Router 只获得 Use Case，不获得 UoW、AsyncSession 或提交接口（SPEC §5.6）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.errors import ConflictError, NotFoundError, ParameterError
from app.events.dispatcher import TransactionalEventDispatcher
from app.modules.audit.domain.diff import compute_diff
from app.modules.user.application.port import (
    LastSuperAdminCheck,
    UserApplicationPort,
    UserUnitOfWork,
)
from app.modules.user.domain.events import UserCreated, UserDisabled
from app.modules.user.domain.model import User
from app.modules.user.domain.password import PasswordHasher
from app.modules.user.domain.policy import PasswordPolicy, UsernamePolicy
from app.ports.audit import AuditDiff, AuditPort, AuditResult
from app.ports.session_lifecycle import SessionLifecyclePort

#: 用户审计差异字段白名单（SPEC §18.2：字段白名单，禁止反射式全字段序列化）。
#:
#: 仅跟踪用户业务字段的变更，不包含 password_hash 等敏感字段
#: （密码、Token 和密钥永不进入差异——SPEC §18.2）。
_USER_AUDIT_FIELDS: tuple[str, ...] = ("display_name", "phone", "email", "status")


class UserService(UserApplicationPort):
    """用户模块应用服务（SPEC §5.2、§11.1）。

    实现用户管理的全部 Use Case。每个写 Use Case 在独立的 Unit of Work
    中执行，退出时统一提交或回滚。

    用户禁用、管理员重置密码和用户自助修改密码时，通过
    :class:`~app.ports.session_lifecycle.SessionLifecyclePort` 在同一事务内
    吊销相关会话（SPEC §12.3）。

    写操作通过 :class:`~app.ports.audit.AuditPort` 显式记录操作审计，
    审计记录与业务数据在同一事务提交（SPEC §5.7、§18.2）。

    Args:
        uow_factory: 工作单元工厂，每次调用返回新的 :class:`UserUnitOfWork`
        password_hasher: Argon2id 密码哈希服务
        last_super_admin_check: 最后一个超级管理员检查端口
        event_dispatcher: 事务内事件调度器
        session_lifecycle: 会话生命周期管理端口（可选，认证模块装配后注入）
        audit_port: 审计端口（可选，审计模块装配后注入）
    """

    def __init__(
        self,
        uow_factory: Callable[[], UserUnitOfWork],
        password_hasher: PasswordHasher,
        last_super_admin_check: LastSuperAdminCheck,
        event_dispatcher: TransactionalEventDispatcher,
        session_lifecycle: SessionLifecyclePort | None = None,
        audit_port: AuditPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._super_admin_check = last_super_admin_check
        self._event_dispatcher = event_dispatcher
        self._session_lifecycle = session_lifecycle
        self._audit_port = audit_port

    # ------------------------------------------------------------------
    # 管理 Use Case
    # ------------------------------------------------------------------

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        phone: str | None = None,
        email: str | None = None,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """创建用户 Use Case（SPEC §11.1）。

        1. 校验用户名和密码（领域策略）
        2. 检查用户名唯一性
        3. 哈希密码
        4. 持久化并发布事件
        5. 显式记录操作审计（SPEC §5.7）
        """
        async with self._uow_factory() as uow:
            try:
                UsernamePolicy.validate(username)
                PasswordPolicy.validate(password)
            except ValueError as exc:
                raise ParameterError(
                    str(exc),
                    code="USER.INVALID_INPUT",
                ) from exc

            existing = await uow.users.get_by_username(username)
            if existing is not None:
                raise ConflictError(
                    "用户名已存在",
                    code="USER.ALREADY_EXISTS",
                )

            password_hash = self._password_hasher.hash(password)
            user = User.new(
                username=username,
                display_name=display_name,
                password_hash=password_hash,
                phone=phone,
                email=email,
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.users.add(user)

            self._event_dispatcher.collect(
                UserCreated(
                    occurred_at=current_time,
                    user_id=user.id,
                    username=user.username,
                )
            )
            await self._event_dispatcher.flush(uow)

            await self._audit_create(
                uow=uow,
                actor_id=actor_id,
                occurred_at=current_time,
                action="user.create",
                resource_id=str(user.id),
                resource_display_name=user.display_name,
            )

            return user

    async def get_user(self, user_id: UUID) -> User:
        """查询用户详情 Use Case（SPEC §11.1）。"""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )
            return user

    async def list_users(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[User], int]:
        """分页查询用户列表 Use Case（SPEC §11.1）。"""
        async with self._uow_factory() as uow:
            total = await uow.users.count()
            offset = (page - 1) * page_size
            users = await uow.users.list_paginated(offset, page_size)
            return users, total

    async def update_user_profile(
        self,
        *,
        user_id: UUID,
        field_updates: dict[str, str | None],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """更新用户基本资料 Use Case（SPEC §11.1）。"""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )

            updated_user = user.with_profile_updates(
                field_updates=field_updates,
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.users.update(updated_user)

            await self._audit_with_diff(
                uow=uow,
                actor_id=actor_id,
                occurred_at=current_time,
                action="user.update",
                resource_id=str(user_id),
                resource_display_name=updated_user.display_name,
                before=self._user_snapshot(user),
                after=self._user_snapshot(updated_user),
            )

            return updated_user

    async def enable_user(
        self,
        *,
        user_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """启用用户 Use Case（SPEC §11.1）。用户状态变更必须审计（§18.2）。"""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )

            enabled_user = user.enable(
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.users.update(enabled_user)

            await self._audit_with_diff(
                uow=uow,
                actor_id=actor_id,
                occurred_at=current_time,
                action="user.enable",
                resource_id=str(user_id),
                resource_display_name=enabled_user.display_name,
                before={"status": str(user.status)},
                after={"status": str(enabled_user.status)},
            )

            return enabled_user

    async def disable_user(
        self,
        *,
        user_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """禁用用户 Use Case（SPEC §11.1、§13.4）。

        禁止禁用系统最后一个可用超级管理员。用户状态变更必须审计（§18.2）。
        """
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )

            # 超级管理员保护检查（SPEC §11.1、§13.4）
            if await self._super_admin_check.is_last_available_super_admin(user_id):
                raise ConflictError(
                    "不能禁用系统最后一个可用超级管理员",
                    code="USER.LAST_SUPER_ADMIN",
                )

            disabled_user = user.disable(
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.users.update(disabled_user)

            # 用户禁用 → 全部会话失效（SPEC §12.3）
            if self._session_lifecycle is not None:
                await self._session_lifecycle.revoke_all_user_sessions(
                    uow,
                    user_id,
                    "user_disabled",
                    current_time,
                )

            self._event_dispatcher.collect(
                UserDisabled(
                    occurred_at=current_time,
                    user_id=disabled_user.id,
                )
            )
            await self._event_dispatcher.flush(uow)

            await self._audit_with_diff(
                uow=uow,
                actor_id=actor_id,
                occurred_at=current_time,
                action="user.disable",
                resource_id=str(user_id),
                resource_display_name=disabled_user.display_name,
                before={"status": str(user.status)},
                after={"status": str(disabled_user.status)},
            )

            return disabled_user

    async def reset_password(
        self,
        *,
        user_id: UUID,
        new_password: str,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """管理员重置用户密码 Use Case（SPEC §11.1）。

        管理员重置密码后认证模块吊销全部会话（SPEC §12.3）。
        """
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )

            try:
                PasswordPolicy.validate(new_password)
            except ValueError as exc:
                raise ParameterError(
                    str(exc),
                    code="USER.INVALID_PASSWORD",
                ) from exc

            new_hash = self._password_hasher.hash(new_password)
            updated_user = user.change_password(
                password_hash=new_hash,
                current_time=current_time,
                actor_id=actor_id,
            )
            await uow.users.update(updated_user)

            # 管理员重置密码 → 吊销全部会话（SPEC §12.3）
            if self._session_lifecycle is not None:
                await self._session_lifecycle.revoke_all_user_sessions(
                    uow,
                    user_id,
                    "password_reset",
                    current_time,
                )

            # 密码变更审计——不记录密码值，仅记录操作发生（SPEC §18.2）
            await self._audit_create(
                uow=uow,
                actor_id=actor_id,
                occurred_at=current_time,
                action="user.reset_password",
                resource_id=str(user_id),
                resource_display_name=updated_user.display_name,
            )

            return updated_user

    # ------------------------------------------------------------------
    # 自助 Use Case
    # ------------------------------------------------------------------

    async def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
        current_time: datetime,
        keep_session_id: UUID | None = None,
    ) -> User:
        """用户自助修改密码 Use Case（SPEC §11.1）。

        需验证当前密码正确。用户主动修改密码时保留当前会话
        并吊销其他会话（SPEC §12.3）。
        """
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )

            # 验证当前密码
            if not self._password_hasher.verify(user.password_hash, current_password):
                raise ParameterError(
                    "当前密码不正确",
                    code="USER.INVALID_CREDENTIALS",
                )

            try:
                PasswordPolicy.validate(new_password)
            except ValueError as exc:
                raise ParameterError(
                    str(exc),
                    code="USER.INVALID_PASSWORD",
                ) from exc

            new_hash = self._password_hasher.hash(new_password)
            updated_user = user.change_password(
                password_hash=new_hash,
                current_time=current_time,
                actor_id=user_id,
            )
            await uow.users.update(updated_user)

            # 用户自助改密 → 保留当前会话、吊销其他（SPEC §12.3）
            if self._session_lifecycle is not None:
                if keep_session_id is not None:
                    await self._session_lifecycle.revoke_user_sessions_except(
                        uow,
                        user_id,
                        keep_session_id,
                        "password_changed",
                        current_time,
                    )
                else:
                    await self._session_lifecycle.revoke_all_user_sessions(
                        uow,
                        user_id,
                        "password_changed",
                        current_time,
                    )

            # 密码变更审计——不记录密码值，仅记录操作发生（SPEC §18.2）
            await self._audit_create(
                uow=uow,
                actor_id=user_id,
                occurred_at=current_time,
                action="user.change_password",
                resource_id=str(user_id),
                resource_display_name=updated_user.display_name,
            )

            return updated_user

    async def get_self_profile(self, user_id: UUID) -> User:
        """用户自助查询资料 Use Case（SPEC §11.1）。"""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )
            return user

    async def update_self_profile(
        self,
        *,
        user_id: UUID,
        field_updates: dict[str, str | None],
        current_time: datetime,
    ) -> User:
        """用户自助更新资料 Use Case（SPEC §11.1）。

        自助更新以当前用户自身为操作者。
        """
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError(
                    "用户不存在",
                    code="USER.NOT_FOUND",
                )

            updated_user = user.with_profile_updates(
                field_updates=field_updates,
                current_time=current_time,
                actor_id=user_id,
            )
            await uow.users.update(updated_user)

            await self._audit_with_diff(
                uow=uow,
                actor_id=user_id,
                occurred_at=current_time,
                action="user.self_update",
                resource_id=str(user_id),
                resource_display_name=updated_user.display_name,
                before=self._user_snapshot(user),
                after=self._user_snapshot(updated_user),
            )

            return updated_user

    # ------------------------------------------------------------------
    # 审计辅助方法（SPEC §5.7、§18.2）
    # ------------------------------------------------------------------

    @staticmethod
    def _user_snapshot(user: User) -> dict[str, object]:
        """从用户实体提取审计白名单字段快照（SPEC §18.2）。

        仅提取白名单字段值用于差异比较，不包含 password_hash 等敏感字段。
        """
        return {
            "display_name": user.display_name,
            "phone": user.phone,
            "email": user.email,
            "status": str(user.status),
        }

    async def _resolve_actor_display_name(
        self,
        uow: UserUnitOfWork,
        actor_id: UUID | None,
    ) -> str | None:
        """从当前事务中查询操作者显示名称快照（SPEC §18.2）。

        操作者显示名称按操作发生时快照保存。在同一事务中读取保证一致性。
        """
        if actor_id is None:
            return None
        actor = await uow.users.get_by_id(actor_id)
        if actor is not None:
            return actor.display_name
        return None

    async def _audit_create(
        self,
        *,
        uow: UserUnitOfWork,
        actor_id: UUID | None,
        occurred_at: datetime,
        action: str,
        resource_id: str,
        resource_display_name: str,
    ) -> None:
        """记录无差异的操作审计（创建、密码重置等，SPEC §5.7、§18.2）。

        审计记录与业务数据在同一事务提交。密码等敏感操作不记录差异
        （SPEC §18.2）。
        """
        if self._audit_port is None:
            return
        await self._audit_port.record(
            uow,
            actor_id=actor_id,
            actor_display_name=await self._resolve_actor_display_name(uow, actor_id),
            occurred_at=occurred_at,
            module="user",
            action=action,
            resource_type="user:account",
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result=AuditResult.SUCCESS,
        )

    async def _audit_with_diff(
        self,
        *,
        uow: UserUnitOfWork,
        actor_id: UUID | None,
        occurred_at: datetime,
        action: str,
        resource_id: str,
        resource_display_name: str,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        """记录带变更差异的操作审计（SPEC §5.7、§18.2）。

        差异使用字段白名单生成，敏感字段永不进入差异（SPEC §18.2）。
        审计记录与业务数据在同一事务提交。
        """
        if self._audit_port is None:
            return
        computed = compute_diff(
            before=before,
            after=after,
            allowed_fields=_USER_AUDIT_FIELDS,
        )
        diff: AuditDiff | None = None if computed.is_empty else computed
        await self._audit_port.record(
            uow,
            actor_id=actor_id,
            actor_display_name=await self._resolve_actor_display_name(uow, actor_id),
            occurred_at=occurred_at,
            module="user",
            action=action,
            resource_type="user:account",
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result=AuditResult.SUCCESS,
            diff=diff,
        )
