"""示例领域事件 — SPEC 5.7.

SPEC 5.7:
  - Domain Event 是不依赖 FastAPI、ORM 和基础设施的不可变对象。
  - 跨模块事件载荷只允许稳定编码、标量值和资源 ID，
    不得携带 ORM 模型或可变领域对象。

事件在 Use Case 执行过程中创建，通过 ``TransactionalEventDispatcher``
在 UoW 提交前同步分发到对应处理器（SPEC 5.7）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.events.events import DomainEvent


@dataclass(frozen=True)
class ExampleItemCreated(DomainEvent):
    """示例条目创建事件 — 在创建 Use Case 的事务内分发.

    SPEC 5.7: "跨模块事件载荷只允许稳定编码、标量值和资源 ID"。
    载荷携带 item_id（UUID 字符串）和 name（字符串），均为标量值。

    属性:
        code: 固定为 ``EXAMPLE.ITEM_CREATED``。
        item_id: 创建的条目 ID（UUID 字符串形式）。
        name: 条目名称。
    """

    item_id: str = ""
    name: str = ""
