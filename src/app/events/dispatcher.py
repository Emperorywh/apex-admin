"""事务内事件调度器（SPEC §5.7）。

按 Use Case 收集领域事件，并在 Unit of Work 提交前将事件调度到
已注册的事务内处理器。处理器在当前事务内同步执行；任一处理器失败时
整个 Use Case 回滚（SPEC §5.7）。

事务内处理器不得执行邮件、Webhook、远程 HTTP 调用或其他不可回滚副作用
（SPEC §5.7）。

使用方式::

    dispatcher = TransactionalEventDispatcher(registry)

    async with uow:
        # ... 业务逻辑 ...
        dispatcher.collect(SomeEvent(...))
        await dispatcher.flush(uow)  # 处理器在提交前执行
    # uow 在无异常退出时提交
"""

from __future__ import annotations

from app.events.base import DomainEvent
from app.events.registry import EventHandlerRegistry
from app.ports.unit_of_work import UnitOfWork


class TransactionalEventDispatcher:
    """事务内事件调度器（SPEC §5.7）。

    每个 Use Case 创建独立的调度器实例，在 Use Case 执行期间收集事件，
    并在调用 :meth:`flush` 时将事件调度到事务内处理器。

    处理器在当前事务内同步执行，与 Use Case 共享同一个 Unit of Work。
    任一处理器失败时抛出异常，异常传播到 ``async with uow:`` 退出时
    触发事务回滚（SPEC §5.7）。

    事件按收集顺序（FIFO）调度。同一事件的多个处理器按处理器编码
    稳定排序执行——但执行顺序不作为语义保证，仅用于测试和日志可复现
    （SPEC §5.7）。

    Args:
        registry: 事件处理器注册表，由模块声明构建。
    """

    def __init__(self, registry: EventHandlerRegistry) -> None:
        self._registry = registry
        self._pending: list[DomainEvent] = []

    def collect(self, event: DomainEvent) -> None:
        """收集领域事件，留待 :meth:`flush` 时调度。

        事件按收集顺序（FIFO）排队。可在 :meth:`flush` 前收集多个事件。

        Args:
            event: 待调度的领域事件。
        """
        self._pending.append(event)

    async def flush(self, uow: UnitOfWork) -> None:
        """执行全部待处理事件的事务内处理器（SPEC §5.7）。

        遍历已收集的事件（FIFO 顺序），对每个事件调用其全部已注册的
        事务内处理器。处理器在当前事务内同步执行，接收同一 Unit of Work。

        任一处理器失败时抛出异常，异常传播到 ``async with uow:`` 退出时
        触发事务回滚。后续事件和处理器不再执行。

        执行开始后清空待处理列表——处理器失败时 Use Case 将回滚，
        待处理事件无需保留。

        Args:
            uow: 当前 Unit of Work，处理器通过它执行事务内数据操作。
        """
        events = self._pending
        self._pending = []

        for event in events:
            for handler in self._registry.get_handlers(event.code):
                await handler.run(event, uow)

    @property
    def pending_count(self) -> int:
        """已收集但尚未调度的事件数量。"""
        return len(self._pending)
