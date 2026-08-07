"""认证模块事务内事件处理器（SPEC §5.7）。

事务内事件处理器在 Unit of Work 提交前同步执行，
不得执行邮件、Webhook 或远程 HTTP 调用等不可回滚副作用（SPEC §5.7）。

认证模块的处理器仅记录结构化日志，供运维和审计使用。
"""

from __future__ import annotations

import logging

from app.events.base import DomainEvent
from app.modules.auth.domain.events import SessionCreated, SessionRevoked
from app.ports.unit_of_work import UnitOfWork

_logger = logging.getLogger("app.modules.auth.event_handlers")


async def handle_session_created(
    event: DomainEvent,
    uow: UnitOfWork,  # noqa: ARG001（事务内处理器签名要求接收 UoW）
) -> None:
    """事务内处理器：记录会话创建事件（SPEC §5.7、§18.1）。"""
    if not isinstance(event, SessionCreated):
        return

    _logger.info(
        "会话已创建（登录成功）",
        extra={
            "event_code": event.code,
            "session_id": str(event.session_id),
            "user_id": str(event.user_id),
        },
    )


async def handle_session_revoked(
    event: DomainEvent,
    uow: UnitOfWork,  # noqa: ARG001（事务内处理器签名要求接收 UoW）
) -> None:
    """事务内处理器：记录会话吊销事件（SPEC §5.7、§18.1）。"""
    if not isinstance(event, SessionRevoked):
        return

    _logger.info(
        "会话已吊销（退出登录）",
        extra={
            "event_code": event.code,
            "session_id": str(event.session_id),
            "user_id": str(event.user_id),
            "reason": event.reason,
        },
    )
