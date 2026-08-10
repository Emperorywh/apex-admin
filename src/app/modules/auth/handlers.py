"""认证模块事务内事件处理器 — SPEC 5.7 / 12.3.

SPEC 5.7:
  - 需要与业务数据强一致的处理器作为事务内事件处理器，
    在当前 Unit of Work 提交前同步执行。
  - 任一事务内处理器失败时，整个 Use Case 回滚。

SPEC 12.3:
  - "用户被禁用后，其有效会话全部失效"。
  - "管理员重置密码后吊销该用户全部会话"。

处理器在 identity 模块的禁用/重置密码 Use Case 事务内被调用，
在当前 AsyncSession 上执行 UPDATE 吊销会话，保证与业务数据强一致
（SPEC 5.7: 同提交、同回滚）。

处理器通过 Composition Root（identity Router 的依赖注入函数）注入到
identity Use Case 的事件分发器中。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import update

from app.core.events.handlers import TransactionalEventHandler
from app.modules.auth.orm import SessionORM

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.events.events import DomainEvent


class RevokeSessionsOnUserDisabled(TransactionalEventHandler):
    """用户禁用时吊销全部活动会话 — SPEC 12.3 / 5.7.

    SPEC 12.3: "用户被禁用后，其有效会话全部失效"。
    监听 ``USER.DISABLED`` 事件，在当前事务内吊销该用户全部未吊销会话。

    SPEC 5.7: 处理器失败时整个 Use Case 回滚——如果会话吊销失败，
    用户禁用操作也回滚，保证一致性。
    """

    @property
    def code(self) -> str:
        """全局唯一的处理器编码."""

        return "AUTH.REVOKE_SESSIONS_ON_DISABLED"

    @property
    def event_code(self) -> str:
        """处理的事件编码."""

        return "USER.DISABLED"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        """在当前事务内吊销用户全部活动会话.

        事件载荷中的 ``user_id`` 为被禁用用户的 UUID 字符串。
        """

        user_id_str = event.payload.get("user_id", "")
        if not user_id_str:
            return

        from uuid import UUID

        user_id: UUID = UUID(user_id_str)
        stmt = (
            update(SessionORM)
            .where(
                SessionORM.user_id == user_id,
                SessionORM.revoked.is_(False),
            )
            .values(revoked=True, revoked_reason="user_disabled")
        )
        await session.execute(stmt)


class RevokeSessionsOnPasswordReset(TransactionalEventHandler):
    """管理员重置密码时吊销全部活动会话 — SPEC 12.3 / 5.7.

    SPEC 12.3: "管理员重置密码后吊销该用户全部会话"。
    监听 ``USER.PASSWORD_RESET_BY_ADMIN`` 事件，在当前事务内吊销
    该用户全部未吊销会话。

    注意: 用户主动修改密码时保留当前会话并吊销其他会话（SPEC 12.3），
    此处理器不处理自助改密场景——自助改密的会话策略由 auth 模块
    内部逻辑实现。
    """

    @property
    def code(self) -> str:
        """全局唯一的处理器编码."""

        return "AUTH.REVOKE_SESSIONS_ON_PASSWORD_RESET"

    @property
    def event_code(self) -> str:
        """处理的事件编码."""

        return "USER.PASSWORD_RESET_BY_ADMIN"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        """在当前事务内吊销用户全部活动会话.

        事件载荷中的 ``user_id`` 为被重置密码用户的 UUID 字符串。
        """

        user_id_str = event.payload.get("user_id", "")
        if not user_id_str:
            return

        from uuid import UUID

        user_id: UUID = UUID(user_id_str)
        stmt = (
            update(SessionORM)
            .where(
                SessionORM.user_id == user_id,
                SessionORM.revoked.is_(False),
            )
            .values(revoked=True, revoked_reason="password_reset_by_admin")
        )
        await session.execute(stmt)
