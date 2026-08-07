"""事务内事件处理器集成测试（SPEC §5.7）。

验证事件系统与 Unit of Work 事务生命周期的集成：
- 处理器在 UoW 提交前同步执行。
- 处理器失败时整个 Use Case 回滚。
- 处理器与 Use Case 共享同一事务。
- 完整 Use Case 模式：收集事件 → flush → async with 退出 → 提交/回滚。

使用桩 UnitOfWork 模拟事务生命周期，无需真实数据库。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import ClassVar, Self

import pytest

from app.events.base import DomainEvent, TransactionalEventHandlerFn
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.contract import EventHandlerDefinition, ModuleDefinition
from app.modules.registry import ModuleRegistry
from app.ports.unit_of_work import UnitOfWork

pytestmark = [pytest.mark.integration, pytest.mark.g1]

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 测试用事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResourceCreated(DomainEvent):
    """测试用事件：资源创建。"""

    code: ClassVar[str] = "integ.resource.created"
    resource_id: str
    resource_code: str
    actor_id: str | None


# ---------------------------------------------------------------------------
# 桩 UoW：模拟事务生命周期并记录数据写入
# ---------------------------------------------------------------------------


class _TrackingUow(UnitOfWork):
    """记录事务操作和数据写入的桩 UoW。

    记录 commit / rollback 调用顺序，以及处理器通过 UoW 写入的"数据"。
    模拟真实 UoW 在 ``__aexit__`` 中的提交/回滚行为。
    """

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.operations: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rolled_back = True
        else:
            self.committed = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    def write(self, operation: str) -> None:
        """模拟数据写入操作（处理器通过 UoW 执行）。"""
        self.operations.append(operation)


def _get_tracking_uow(uow: UnitOfWork) -> _TrackingUow:
    """从 UnitOfWork 提取 _TrackingUow（测试辅助）。"""
    assert isinstance(uow, _TrackingUow)
    return uow


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_dispatcher(
    handlers: dict[str, tuple[str, TransactionalEventHandlerFn]],
) -> TransactionalEventDispatcher:
    """从 {handler_code: (event_code, fn)} 映射构造调度器。"""
    defs = frozenset(
        EventHandlerDefinition(code=code, event_code=event_code, description="")
        for code, (event_code, _fn) in handlers.items()
    )
    fns = {code: fn for code, (_event_code, fn) in handlers.items()}
    module = ModuleDefinition(
        code="INTEG",
        name="集成测试模块",
        description="集成测试",
        application_port=type("_Port", (), {}),
        api_tag="INTEG",
        event_handlers=defs,
    )
    registry = EventHandlerRegistry(ModuleRegistry([module]), fns)
    return TransactionalEventDispatcher(registry)


# ---------------------------------------------------------------------------
# 集成测试：处理器在提交前执行
# ---------------------------------------------------------------------------


class TestHandlerExecutesBeforeCommit:
    """事务内处理器在 UoW 提交前同步执行（SPEC §5.7）。"""

    async def test_handler_runs_before_commit(self) -> None:
        """处理器在 async with 退出（提交）前执行。"""
        log: list[str] = []

        async def audit_handler(event: DomainEvent, uow: UnitOfWork) -> None:
            tracking = _get_tracking_uow(uow)
            log.append("handler_executed")
            tracking.write(f"audit:{event.resource_id}")

        dispatcher = _make_dispatcher(
            {"integ.audit_created": ("integ.resource.created", audit_handler)}
        )
        uow = _TrackingUow()

        async with uow:
            dispatcher.collect(
                _ResourceCreated(
                    occurred_at=_NOW,
                    resource_id="r-001",
                    resource_code="RES_001",
                    actor_id="u-100",
                )
            )
            await dispatcher.flush(uow)
            # flush 后处理器已执行，但尚未提交
            assert log == ["handler_executed"]
            assert uow.operations == ["audit:r-001"]
            assert not uow.committed

        # async with 正常退出后提交
        assert uow.committed
        assert not uow.rolled_back

    async def test_handler_writes_share_transaction(self) -> None:
        """处理器写入与 Use Case 共享同一事务。"""

        async def handler(event: DomainEvent, uow: UnitOfWork) -> None:
            tracking = _get_tracking_uow(uow)
            tracking.write(f"handler_write:{event.resource_code}")

        dispatcher = _make_dispatcher({"integ.handler": ("integ.resource.created", handler)})
        uow = _TrackingUow()

        async with uow:
            uow.write("usecase_write")
            dispatcher.collect(
                _ResourceCreated(
                    occurred_at=_NOW,
                    resource_id="r-001",
                    resource_code="RES_001",
                    actor_id=None,
                )
            )
            await dispatcher.flush(uow)

        # Use Case 写入和处理器写入都在同一事务中
        assert uow.operations == ["usecase_write", "handler_write:RES_001"]
        assert uow.committed


# ---------------------------------------------------------------------------
# 集成测试：处理器失败回滚整个 Use Case
# ---------------------------------------------------------------------------


class TestHandlerFailureRollback:
    """任一处理器失败时整个 Use Case 回滚（SPEC §5.7）。"""

    async def test_handler_failure_rolls_back(self) -> None:
        """处理器失败 → 异常传播 → UoW 回滚。"""

        async def failing_handler(event: DomainEvent, uow: UnitOfWork) -> None:
            tracking = _get_tracking_uow(uow)
            tracking.write("partial_write")
            raise RuntimeError("审计处理器失败")

        dispatcher = _make_dispatcher(
            {"integ.failing": ("integ.resource.created", failing_handler)}
        )
        uow = _TrackingUow()

        with pytest.raises(RuntimeError, match="审计处理器失败"):
            async with uow:
                uow.write("usecase_write")
                dispatcher.collect(
                    _ResourceCreated(
                        occurred_at=_NOW,
                        resource_id="r-001",
                        resource_code="RES_001",
                        actor_id="u-100",
                    )
                )
                await dispatcher.flush(uow)

        # 异常传播到 __aexit__ → 回滚
        assert uow.rolled_back
        assert not uow.committed

    async def test_second_handler_not_executed_after_failure(self) -> None:
        """第一个处理器失败后，第二个处理器不执行。"""
        executed: list[str] = []

        async def first_handler(event: DomainEvent, uow: UnitOfWork) -> None:
            executed.append("first")
            raise RuntimeError("失败")

        async def second_handler(event: DomainEvent, uow: UnitOfWork) -> None:
            executed.append("second")

        dispatcher = _make_dispatcher(
            {
                "integ.first": ("integ.resource.created", first_handler),
                "integ.second": ("integ.resource.created", second_handler),
            }
        )
        uow = _TrackingUow()

        with pytest.raises(RuntimeError):
            async with uow:
                dispatcher.collect(
                    _ResourceCreated(
                        occurred_at=_NOW,
                        resource_id="r-001",
                        resource_code="RES_001",
                        actor_id=None,
                    )
                )
                await dispatcher.flush(uow)

        # 只有 first 执行（按编码排序，integ.first < integ.second）
        assert executed == ["first"]
        assert uow.rolled_back

    async def test_usecase_exception_prevents_flush(self) -> None:
        """Use Case 体内异常时 flush 不执行。"""
        executed: list[str] = []

        async def handler(event: DomainEvent, uow: UnitOfWork) -> None:
            executed.append("handler")

        dispatcher = _make_dispatcher({"integ.handler": ("integ.resource.created", handler)})
        uow = _TrackingUow()

        with pytest.raises(ValueError, match="业务校验失败"):
            async with uow:
                dispatcher.collect(
                    _ResourceCreated(
                        occurred_at=_NOW,
                        resource_id="r-001",
                        resource_code="RES_001",
                        actor_id=None,
                    )
                )
                # Use Case 体内抛出异常，flush 尚未执行
                raise ValueError("业务校验失败")

        assert executed == []
        assert uow.rolled_back
        assert not uow.committed


# ---------------------------------------------------------------------------
# 集成测试：多处理器场景
# ---------------------------------------------------------------------------


class TestMultipleHandlers:
    """多处理器场景（SPEC §5.7）。"""

    async def test_multiple_handlers_all_execute_before_commit(self) -> None:
        """同一事件的多个处理器全部在提交前执行。"""
        log: list[str] = []

        async def handler_a(event: DomainEvent, uow: UnitOfWork) -> None:
            tracking = _get_tracking_uow(uow)
            log.append("a")
            tracking.write("op_a")

        async def handler_b(event: DomainEvent, uow: UnitOfWork) -> None:
            tracking = _get_tracking_uow(uow)
            log.append("b")
            tracking.write("op_b")

        async def handler_c(event: DomainEvent, uow: UnitOfWork) -> None:
            tracking = _get_tracking_uow(uow)
            log.append("c")
            tracking.write("op_c")

        dispatcher = _make_dispatcher(
            {
                "integ.c_handler": ("integ.resource.created", handler_c),
                "integ.a_handler": ("integ.resource.created", handler_a),
                "integ.b_handler": ("integ.resource.created", handler_b),
            }
        )
        uow = _TrackingUow()

        async with uow:
            dispatcher.collect(
                _ResourceCreated(
                    occurred_at=_NOW,
                    resource_id="r-001",
                    resource_code="RES_001",
                    actor_id="u-100",
                )
            )
            await dispatcher.flush(uow)

        # 全部按编码稳定排序执行
        assert log == ["a", "b", "c"]
        assert uow.operations == ["op_a", "op_b", "op_c"]
        assert uow.committed

    async def test_multiple_events_dispatched_in_collection_order(self) -> None:
        """多个事件按收集顺序调度，各自处理器独立执行。"""
        log: list[str] = []

        @dataclass(frozen=True)
        class _ResourceDeleted(DomainEvent):
            code: ClassVar[str] = "integ.resource.deleted"
            resource_id: str

        async def created_handler(event: DomainEvent, uow: UnitOfWork) -> None:
            log.append("created")

        async def deleted_handler(event: DomainEvent, uow: UnitOfWork) -> None:
            log.append("deleted")

        dispatcher = _make_dispatcher(
            {
                "integ.on_created": ("integ.resource.created", created_handler),
                "integ.on_deleted": ("integ.resource.deleted", deleted_handler),
            }
        )
        uow = _TrackingUow()

        async with uow:
            dispatcher.collect(
                _ResourceCreated(
                    occurred_at=_NOW,
                    resource_id="r-001",
                    resource_code="C",
                    actor_id=None,
                )
            )
            dispatcher.collect(_ResourceDeleted(occurred_at=_NOW, resource_id="r-001"))
            await dispatcher.flush(uow)

        assert log == ["created", "deleted"]
        assert uow.committed
