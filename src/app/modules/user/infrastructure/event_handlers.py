"""用户模块事务内事件处理器（SPEC §5.7）。

事务内事件处理器在 Unit of Work 提交前同步执行，
不得执行邮件、Webhook 或远程 HTTP 调用等不可回滚副作用（SPEC §5.7）。

用户模块的处理器仅记录结构化日志，供运维和调试使用。
认证模块（TASK-015）可注册额外的事务内处理器响应 ``user.disabled``
事件来吊销会话。
"""

from __future__ import annotations

import logging

from app.events.base import DomainEvent
from app.modules.user.domain.events import UserCreated, UserDisabled
from app.ports.unit_of_work import UnitOfWork

_logger = logging.getLogger("app.modules.user.event_handlers")


async def handle_user_created(
    event: DomainEvent,
    uow: UnitOfWork,  # noqa: ARG001（事务内处理器签名要求接收 UoW）
) -> None:
    """事务内处理器：记录用户创建事件（SPEC §5.7）。

    在创建用户的 Use Case 提交前同步执行。处理器仅记录日志，
    不执行不可回滚的副作用。

    Args:
        event: 领域事件（预期为 :class:`UserCreated`）
        uow: 当前 Unit of Work
    """
    if not isinstance(event, UserCreated):
        return

    _logger.info(
        "用户已创建",
        extra={
            "event_code": event.code,
            "user_id": str(event.user_id),
            "username": event.username,
        },
    )


async def handle_user_disabled(
    event: DomainEvent,
    uow: UnitOfWork,  # noqa: ARG001（事务内处理器签名要求接收 UoW）
) -> None:
    """事务内处理器：记录用户禁用事件（SPEC §5.7）。

    在禁用用户的 Use Case 提交前同步执行。处理器仅记录日志，
    不执行不可回滚的副作用。

    Args:
        event: 领域事件（预期为 :class:`UserDisabled`）
        uow: 当前 Unit of Work
    """
    if not isinstance(event, UserDisabled):
        return

    _logger.info(
        "用户已禁用",
        extra={
            "event_code": event.code,
            "user_id": str(event.user_id),
        },
    )
