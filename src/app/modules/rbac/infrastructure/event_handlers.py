"""RBAC 模块事务内事件处理器（SPEC §5.7）。

事务内处理器在 Unit of Work 提交前同步执行，失败时整个 Use Case 回滚
（SPEC §5.7）。处理器不得执行不可回滚副作用。

当前实现为占位日志——审计日志持久化（G3）由审计模块独立实现。
"""

from __future__ import annotations

import logging

from app.events.base import DomainEvent
from app.ports.unit_of_work import UnitOfWork

_logger = logging.getLogger("app.modules.rbac.event_handlers")


async def handle_role_created(event: DomainEvent, uow: UnitOfWork) -> None:
    """处理角色创建事件（SPEC §5.7）。"""
    _logger.info(
        "角色创建",
        extra={
            "event_code": event.code,
            "role_id": str(getattr(event, "role_id", "")),
        },
    )


async def handle_role_disabled(event: DomainEvent, uow: UnitOfWork) -> None:
    """处理角色禁用事件（SPEC §5.7）。"""
    _logger.info(
        "角色禁用",
        extra={
            "event_code": event.code,
            "role_id": str(getattr(event, "role_id", "")),
        },
    )


async def handle_user_role_assigned(event: DomainEvent, uow: UnitOfWork) -> None:
    """处理用户角色分配事件（SPEC §5.7）。"""
    _logger.info(
        "用户角色分配",
        extra={
            "event_code": event.code,
            "user_id": str(getattr(event, "user_id", "")),
            "role_id": str(getattr(event, "role_id", "")),
        },
    )


async def handle_user_role_removed(event: DomainEvent, uow: UnitOfWork) -> None:
    """处理用户角色移除事件（SPEC §5.7）。"""
    _logger.info(
        "用户角色移除",
        extra={
            "event_code": event.code,
            "user_id": str(getattr(event, "user_id", "")),
            "role_id": str(getattr(event, "role_id", "")),
        },
    )
