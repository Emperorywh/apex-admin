"""领域事件基类与处理器函数类型（SPEC §5.7）。

Domain Event 是不依赖 FastAPI、ORM 和基础设施的不可变对象。
跨模块事件载荷只允许稳定编码、标量值和资源 ID，不得携带 ORM 模型
或可变领域对象（SPEC §5.7）。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from datetime import date, datetime
from enum import Enum
from typing import ClassVar

from app.ports.unit_of_work import UnitOfWork

# ---------------------------------------------------------------------------
# 事件载荷允许的类型（SPEC §5.7）
#
# 跨模块事件载荷只允许稳定编码、标量值和资源 ID。
# 此元组列出可直接作为载荷字段值的类型，在 __post_init__ 中校验。
# tuple 和 frozenset 作为不可变容器也被允许（内部元素不做递归校验）。
# ---------------------------------------------------------------------------

#: 事件载荷允许的类型（SPEC §5.7）。
#:
#: 跨模块事件载荷只允许稳定编码、标量值和资源 ID。
#: 此元组列出可直接作为载荷字段值的类型（标量、时间、UUID、枚举）
#: 以及不可变容器类型（tuple、frozenset），在 __post_init__ 中校验。
_ALLOWED_PAYLOAD_TYPES: tuple[type, ...] = (
    str,
    int,
    float,
    bool,
    bytes,
    type(None),
    datetime,
    date,
    uuid.UUID,
    Enum,
    tuple,
    frozenset,
)


@dataclass(frozen=True)
class DomainEvent:
    """不可变领域事件基类（SPEC §5.7）。

    不依赖 FastAPI、ORM 或任何基础设施。跨模块事件载荷只允许
    稳定编码、标量值和资源 ID，不得携带 ORM 模型或可变领域对象。

    子类通过添加 dataclass 字段定义事件载荷，并通过类属性 ``code``
    声明事件编码（匹配 :class:`~app.modules.contract.EventDefinition.code`）。

    Example::

        @dataclass(frozen=True)
        class UserCreated(DomainEvent):
            code: ClassVar[str] = "identity.user.created"
            user_id: str
            username: str

    Attributes:
        occurred_at: 事件发生时间（UTC）。
    """

    occurred_at: datetime

    #: 事件编码——子类必须覆写为匹配 EventDefinition.code 的稳定编码。
    code: ClassVar[str] = ""

    def __post_init__(self) -> None:
        """校验事件载荷字段均为允许的不可变类型（SPEC §5.7）。

        遍历所有 dataclass 字段值，拒绝 dict、list、set 等可变容器和
        ORM 模型等不允许的类型。
        """
        for field_obj in fields(self):
            value = getattr(self, field_obj.name)
            if isinstance(value, _ALLOWED_PAYLOAD_TYPES):
                continue
            raise TypeError(
                f"事件载荷字段 '{field_obj.name}' 包含不允许的类型 "
                f"{type(value).__name__}：跨模块事件载荷只允许稳定编码、"
                f"标量值和资源 ID（SPEC §5.7）"
            )


#: 事务内事件处理器函数类型（SPEC §5.7）。
#:
#: 接收领域事件和当前 Unit of Work，在 UoW 提交前同步执行。
#: 处理器失败时抛出异常，导致整个 Use Case 回滚（SPEC §5.7）。
#: 事务内处理器不得执行邮件、Webhook、远程 HTTP 调用等不可回滚副作用。
TransactionalEventHandlerFn = Callable[[DomainEvent, UnitOfWork], Awaitable[None]]
