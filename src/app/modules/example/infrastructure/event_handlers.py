"""示例模块事务内事件处理器（SPEC §5.7）。

事务内事件处理器在 Unit of Work 提交前同步执行，
不得执行邮件、Webhook 或远程 HTTP 调用等不可回滚副作用（SPEC §5.7）。

示例处理器仅记录结构化日志，演示处理器声明与实现的对应关系。
"""

from __future__ import annotations

import logging

from app.events.base import DomainEvent
from app.modules.example.domain.events import ExampleItemCreated
from app.ports.unit_of_work import UnitOfWork

_logger = logging.getLogger("app.modules.example.event_handlers")


async def handle_example_item_created(
    event: DomainEvent,
    uow: UnitOfWork,  # noqa: ARG001（事务内处理器签名要求接收 UoW）
) -> None:
    """事务内处理器：记录示例项目创建事件（SPEC §5.7）。

    在创建示例项目的 Use Case 提交前同步执行。处理器仅记录日志，
    不执行不可回滚的副作用。

    Args:
        event: 领域事件（预期为 :class:`ExampleItemCreated`）
        uow: 当前 Unit of Work（处理器可在同一事务内执行数据操作）
    """
    if not isinstance(event, ExampleItemCreated):
        return

    _logger.info(
        "示例项目已创建",
        extra={
            "event_code": event.code,
            "item_id": str(event.item_id),
            "name": event.name,
        },
    )
