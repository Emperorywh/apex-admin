"""示例领域事件（SPEC §5.7）。

领域事件是不依赖 FastAPI、ORM 和基础设施的不可变对象。
跨模块事件载荷只允许稳定编码、标量值和资源 ID（SPEC §5.7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.events.base import DomainEvent


@dataclass(frozen=True)
class ExampleItemCreated(DomainEvent):
    """示例项目创建事件。

    在创建示例项目成功后由 Use Case 发布，事务内事件处理器在
    Unit of Work 提交前同步执行（SPEC §5.7）。

    Attributes:
        occurred_at: 事件发生时间（UTC），继承自 :class:`DomainEvent`
        item_id: 被创建项目的 UUID
        name: 被创建项目的名称
    """

    code: ClassVar[str] = "example.item.created"

    item_id: UUID
    name: str
