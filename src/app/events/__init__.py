"""事件系统——领域事件与事务内处理器（SPEC §5.7）。

本包提供领域事件基础设施：

- :class:`DomainEvent`：不可变、不依赖基础设施的领域事件基类。
- :class:`EventHandlerRegistry`：从已校验的模块注册表构建的事务内处理器注册表。
- :class:`TransactionalEventDispatcher`：按 Use Case 收集事件并在 UoW 提交前调度。

事件仅用于解除已确认的模块依赖，不是普通函数调用的替代品（SPEC §5.7）。
"""

from __future__ import annotations

from app.events.base import DomainEvent, TransactionalEventHandlerFn
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import (
    EventHandlerRegistrationError,
    EventHandlerRegistry,
    RegisteredHandler,
)

__all__ = [
    "DomainEvent",
    "EventHandlerRegistrationError",
    "EventHandlerRegistry",
    "RegisteredHandler",
    "TransactionalEventDispatcher",
    "TransactionalEventHandlerFn",
]
