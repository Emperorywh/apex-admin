"""事务内事件分发器 — SPEC 5.7.

SPEC 5.7:
  - 事务内事件处理器在当前 Unit of Work 提交前同步执行。
  - 任一事务内处理器失败时，整个 Use Case 回滚。
  - 多处理器不得依赖执行顺序；稳定排序只用于保证测试和日志可复现。

分发器由 Composition Root 构造：收集所有已启用模块的事务内事件处理器，
注入到分发器实例。Use Case 在执行过程中调用 ``collect`` 收集事件，
在 ``uow.commit()`` 前调用 ``dispatch`` 同步执行所有匹配的处理器。

处理器执行顺序: 按 (handler_code, event_code) 字典序稳定排序，
仅用于保证测试和日志可复现（SPEC 5.7: "多处理器不得依赖执行顺序"）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.events.events import DomainEvent
    from app.core.events.handlers import TransactionalEventHandler


class TransactionalEventDispatcher:
    """事务内事件分发器 — 收集事件并在 UoW 提交前同步分发.

    SPEC 5.7: "需要与业务数据强一致的处理器作为事务内事件处理器，
    在当前 Unit of Work 提交前同步执行"。

    使用方式::

        dispatcher = TransactionalEventDispatcher(handlers)

        async with uow:
            # 执行业务操作
            session = uow.session
            # 收集事件
            dispatcher.collect(SomeEvent(code="USER.CREATED"))
            # 在 commit 前同步分发
            await dispatcher.dispatch(session)
            await uow.commit()

    处理器匹配规则: 当事件 ``code`` 与处理器的 ``event_code`` 相等时，
    该处理器处理该事件。分发器按稳定排序执行所有匹配的处理器。

    回滚保证: 处理器在 commit 前执行，任一处理器失败时异常传播到
    Use Case，UoW 的 ``__aexit__`` 自动回滚整个事务
    （SPEC 5.7: "任一事务内处理器失败时，整个 Use Case 回滚"）。
    """

    def __init__(
        self,
        handlers: list[TransactionalEventHandler],
    ) -> None:
        """初始化分发器，注册处理器.

        参数:
            handlers: 已启用模块提供的事务内事件处理器列表。
                      Composition Root 收集所有模块的处理器后注入。
        """

        # 按处理器编码稳定排序，保证测试和日志可复现。
        # SPEC 5.7: "多处理器不得依赖执行顺序"。
        self._handlers: list[TransactionalEventHandler] = sorted(
            handlers,
            key=lambda h: (h.code, h.event_code),
        )
        self._events: list[DomainEvent] = []

    @property
    def handlers(self) -> list[TransactionalEventHandler]:
        """返回已注册的处理器列表（稳定排序后的只读副本）。"""

        return list(self._handlers)

    @property
    def pending_events(self) -> list[DomainEvent]:
        """返回已收集但尚未分发的事件列表。"""

        return list(self._events)

    def collect(self, event: DomainEvent) -> None:
        """收集事件，等待分发.

        SPEC 5.7: 事件在 Use Case 执行过程中产生，
        在 UoW 提交前同步分发。

        参数:
            event: 待分发的领域事件。
        """

        self._events.append(event)

    def clear(self) -> None:
        """清空已收集的事件.

        用于测试或异常恢复后重置分发器状态。
        """

        self._events.clear()

    async def dispatch(self, session: AsyncSession) -> None:
        """同步分发所有已收集事件到匹配处理器.

        SPEC 5.7: 处理器在 UoW 提交前同步执行。
        任一处理器失败时异常传播到 Use Case，触发事务回滚。

        分发顺序: 事件按 code 稳定排序，处理器按 (code, event_code)
        稳定排序。此顺序仅用于测试和日志可复现，不构成业务正确性依赖。

        参数:
            session: 当前 UoW 拥有的 AsyncSession。
                     处理器在事务内执行数据库操作。

        抛出:
            处理器抛出的任何异常，传播到 Use Case 触发回滚。
        """

        # 事件按 code 稳定排序，保证可复现性
        sorted_events = sorted(self._events, key=lambda e: e.code)

        for event in sorted_events:
            for handler in self._handlers:
                if handler.event_code == event.code:
                    # 处理器在事务内同步执行。
                    # 失败时异常传播，UoW __aexit__ 自动回滚。
                    await handler.handle(event, session)
