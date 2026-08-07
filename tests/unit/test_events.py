"""领域事件系统单元测试（SPEC §5.7）。

覆盖验收条件：
- DomainEvent 为不可变、不依赖基础设施的 dataclass。
- 跨模块事件载荷只包含稳定编码、标量值和资源 ID。
- 事务内处理器在 UoW 提交前同步执行；任一处理器失败回滚整个 Use Case。
- 处理器通过 ModuleDefinition 注册；重复事件编码或处理器编码 → 启动失败。
- 多处理器执行顺序不保证（稳定排序用于测试/日志）。
- 事务内处理器不执行邮件、Webhook 或其他不可回滚副作用。
"""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, dataclass, is_dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import ClassVar, Self

import pytest

from app.events.base import DomainEvent, TransactionalEventHandlerFn
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import (
    EventHandlerRegistrationError,
    EventHandlerRegistry,
)
from app.modules.contract import (
    EventDefinition,
    EventHandlerDefinition,
    ModuleDefinition,
)
from app.modules.registry import ModuleRegistry
from app.ports.unit_of_work import UnitOfWork

pytestmark = [pytest.mark.unit, pytest.mark.g1]

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 测试用事件子类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ItemCreated(DomainEvent):
    """测试用事件：资源创建。"""

    code: ClassVar[str] = "test.item.created"
    item_id: str
    item_code: str


@dataclass(frozen=True)
class _ItemUpdated(DomainEvent):
    """测试用事件：资源更新。"""

    code: ClassVar[str] = "test.item.updated"
    item_id: str
    new_name: str


# ---------------------------------------------------------------------------
# 测试用 UoW 桩
# ---------------------------------------------------------------------------


class _FakeUow(UnitOfWork):
    """记录提交和回滚调用的 UnitOfWork 桩。"""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

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


# ---------------------------------------------------------------------------
# 测试辅助函数
# ---------------------------------------------------------------------------


def _make_module(
    code: str = "TEST",
    events: frozenset[EventDefinition] = frozenset(),
    event_handlers: frozenset[EventHandlerDefinition] = frozenset(),
) -> ModuleDefinition:
    """构造测试用 ModuleDefinition。"""
    return ModuleDefinition(
        code=code,
        name=f"模块 {code}",
        description=f"测试模块 {code}",
        application_port=type("_Port", (), {}),
        api_tag=code,
        events=events,
        event_handlers=event_handlers,
    )


def _make_registry(
    modules: list[ModuleDefinition],
    handler_fns: dict[str, TransactionalEventHandlerFn] | None = None,
) -> EventHandlerRegistry:
    """构造测试用 EventHandlerRegistry。"""
    return EventHandlerRegistry(ModuleRegistry(modules), handler_fns or {})


def _make_dispatcher(
    handler_defs: frozenset[EventHandlerDefinition],
    handler_fns: dict[str, TransactionalEventHandlerFn],
) -> TransactionalEventDispatcher:
    """构造包含指定处理器声明的调度器。"""
    module = _make_module(event_handlers=handler_defs)
    registry = EventHandlerRegistry(ModuleRegistry([module]), handler_fns)
    return TransactionalEventDispatcher(registry)


async def _noop_handler(event: DomainEvent, uow: UnitOfWork) -> None:
    """空操作处理器。"""


# ---------------------------------------------------------------------------
# DomainEvent 不可变性测试（验收条件 #0）
# ---------------------------------------------------------------------------


class TestDomainEventImmutable:
    """DomainEvent 为不可变、不依赖基础设施的 dataclass（SPEC §5.7）。"""

    def test_domain_event_is_frozen_dataclass(self) -> None:
        """DomainEvent 使用 frozen=True dataclass。"""
        assert is_dataclass(DomainEvent)
        event = _ItemCreated(occurred_at=_NOW, item_id="i-001", item_code="ITEM_001")
        with pytest.raises(FrozenInstanceError):
            event.item_id = "i-999"  # type: ignore[misc]

    def test_occurred_at_also_immutable(self) -> None:
        """occurred_at 字段也不可变。"""
        event = _ItemCreated(occurred_at=_NOW, item_id="i-001", item_code="C")
        with pytest.raises(FrozenInstanceError):
            event.occurred_at = datetime(2025, 1, 1, tzinfo=UTC)  # type: ignore[misc]

    def test_base_domain_event_cannot_set_attribute(self) -> None:
        """基类实例同样不可变。"""
        event = DomainEvent(occurred_at=_NOW)
        with pytest.raises(FrozenInstanceError):
            event.occurred_at = _NOW  # type: ignore[misc]

    def test_no_infrastructure_imports(self) -> None:
        """events.base 模块不导入基础设施包（FastAPI、SQLAlchemy 等）。"""
        import app.events.base as base_mod

        # 检查模块实际导入的包，而非源码文本（文档可能提到框架名）
        forbidden_prefixes = ("fastapi", "sqlalchemy", "uvicorn", "alembic", "psycopg")
        for name in dir(base_mod):
            obj = getattr(base_mod, name)
            module = getattr(obj, "__module__", None)
            if module and any(module.startswith(p) for p in forbidden_prefixes):
                pytest.fail(f"events.base 间接依赖了基础设施包: {module}")

        # 直接检查 sys.modules 中是否有被 events.base 触发的基础设施导入
        import sys

        base_module = sys.modules["app.events.base"]
        # events.base 只应导入 stdlib 和 app.ports.unit_of_work
        assert "fastapi" not in base_module.__dict__
        assert "sqlalchemy" not in base_module.__dict__


# ---------------------------------------------------------------------------
# 事件载荷校验测试（验收条件 #1）
# ---------------------------------------------------------------------------


class TestPayloadValidation:
    """跨模块事件载荷只允许稳定编码、标量值和资源 ID（SPEC §5.7）。"""

    def test_scalar_string_payload_allowed(self) -> None:
        """字符串载荷允许。"""
        event = _ItemCreated(occurred_at=_NOW, item_id="i-001", item_code="CODE")
        assert event.item_id == "i-001"

    def test_scalar_int_payload_allowed(self) -> None:
        """整数载荷允许。"""

        @dataclass(frozen=True)
        class _CountEvent(DomainEvent):
            code: ClassVar[str] = "test.count"
            count: int

        event = _CountEvent(occurred_at=_NOW, count=42)
        assert event.count == 42

    def test_scalar_float_payload_allowed(self) -> None:
        """浮点载荷允许。"""

        @dataclass(frozen=True)
        class _MetricEvent(DomainEvent):
            code: ClassVar[str] = "test.metric"
            value: float

        event = _MetricEvent(occurred_at=_NOW, value=3.14)
        assert event.value == 3.14

    def test_bool_payload_allowed(self) -> None:
        """布尔载荷允许。"""

        @dataclass(frozen=True)
        class _FlagEvent(DomainEvent):
            code: ClassVar[str] = "test.flag"
            enabled: bool

        event = _FlagEvent(occurred_at=_NOW, enabled=True)
        assert event.enabled is True

    def test_none_payload_allowed(self) -> None:
        """None 值允许（Optional 字段）。"""

        @dataclass(frozen=True)
        class _OptionalEvent(DomainEvent):
            code: ClassVar[str] = "test.optional"
            ref_id: str | None

        event = _OptionalEvent(occurred_at=_NOW, ref_id=None)
        assert event.ref_id is None

    def test_datetime_payload_allowed(self) -> None:
        """datetime 载荷允许。"""

        @dataclass(frozen=True)
        class _TimeEvent(DomainEvent):
            code: ClassVar[str] = "test.time"
            scheduled_at: datetime

        event = _TimeEvent(occurred_at=_NOW, scheduled_at=_NOW)
        assert event.scheduled_at == _NOW

    def test_uuid_payload_allowed(self) -> None:
        """UUID 载荷允许（资源 ID）。"""

        @dataclass(frozen=True)
        class _UuidEvent(DomainEvent):
            code: ClassVar[str] = "test.uuid"
            resource_id: uuid.UUID

        rid = uuid.uuid4()
        event = _UuidEvent(occurred_at=_NOW, resource_id=rid)
        assert event.resource_id == rid

    def test_tuple_payload_allowed(self) -> None:
        """tuple（不可变容器）载荷允许。"""

        @dataclass(frozen=True)
        class _TagsEvent(DomainEvent):
            code: ClassVar[str] = "test.tags"
            tags: tuple[str, ...]

        event = _TagsEvent(occurred_at=_NOW, tags=("a", "b"))
        assert event.tags == ("a", "b")

    def test_frozenset_payload_allowed(self) -> None:
        """frozenset（不可变容器）载荷允许。"""

        @dataclass(frozen=True)
        class _SetEvent(DomainEvent):
            code: ClassVar[str] = "test.set"
            codes: frozenset[str]

        event = _SetEvent(occurred_at=_NOW, codes=frozenset({"x", "y"}))
        assert event.codes == frozenset({"x", "y"})

    def test_dict_payload_rejected(self) -> None:
        """dict（可变容器）载荷被拒绝。"""

        @dataclass(frozen=True)
        class _DictEvent(DomainEvent):
            code: ClassVar[str] = "test.dict"
            data: dict[str, str]  # type: ignore[assignment]

        with pytest.raises(TypeError, match="不允许的类型 dict"):
            _DictEvent(occurred_at=_NOW, data={"k": "v"})

    def test_list_payload_rejected(self) -> None:
        """list（可变容器）载荷被拒绝。"""

        @dataclass(frozen=True)
        class _ListEvent(DomainEvent):
            code: ClassVar[str] = "test.list"
            items: list[str]  # type: ignore[assignment]

        with pytest.raises(TypeError, match="不允许的类型 list"):
            _ListEvent(occurred_at=_NOW, items=["a", "b"])

    def test_set_payload_rejected(self) -> None:
        """set（可变容器）载荷被拒绝。"""

        @dataclass(frozen=True)
        class _SetPayloadEvent(DomainEvent):
            code: ClassVar[str] = "test.setpayload"
            items: set[str]  # type: ignore[assignment]

        with pytest.raises(TypeError, match="不允许的类型 set"):
            _SetPayloadEvent(occurred_at=_NOW, items={"a", "b"})


# ---------------------------------------------------------------------------
# 事件编码测试
# ---------------------------------------------------------------------------


class TestEventCode:
    """DomainEvent 事件编码通过 ClassVar 声明。"""

    def test_subclass_code_accessible(self) -> None:
        """子类 code 类属性可访问。"""
        event = _ItemCreated(occurred_at=_NOW, item_id="i-1", item_code="C")
        assert event.code == "test.item.created"

    def test_base_class_code_is_empty_string(self) -> None:
        """基类 code 默认为空字符串。"""
        event = DomainEvent(occurred_at=_NOW)
        assert event.code == ""

    def test_different_events_have_different_codes(self) -> None:
        """不同事件子类有不同编码。"""
        assert _ItemCreated.code != _ItemUpdated.code


# ---------------------------------------------------------------------------
# EventHandlerRegistry 测试（验收条件 #3、#4）
# ---------------------------------------------------------------------------


class TestEventHandlerRegistry:
    """事件处理器注册表构建与查询。"""

    def test_empty_registry_returns_no_handlers(self) -> None:
        """无声明时注册表为空。"""
        registry = _make_registry([_make_module()])
        assert registry.get_handlers("test.item.created") == []
        assert registry.event_codes == frozenset()
        assert registry.handler_codes == frozenset()

    def test_registry_indexes_handlers_by_event_code(self) -> None:
        """处理器按事件编码索引。"""
        handler_def = EventHandlerDefinition(
            code="audit.on_created",
            event_code="test.item.created",
            description="创建审计",
        )
        module = _make_module(event_handlers=frozenset({handler_def}))
        registry = _make_registry([module], {"audit.on_created": _noop_handler})

        handlers = registry.get_handlers("test.item.created")
        assert len(handlers) == 1
        assert handlers[0].code == "audit.on_created"
        assert handlers[0].event_code == "test.item.created"
        assert registry.event_codes == frozenset({"test.item.created"})

    def test_non_transactional_handlers_ignored(self) -> None:
        """非事务内处理器（transactional=False）不被注册。"""
        handler_def = EventHandlerDefinition(
            code="ext.on_created",
            event_code="test.item.created",
            description="事务后处理器",
            transactional=False,
        )
        module = _make_module(event_handlers=frozenset({handler_def}))
        registry = _make_registry([module])

        assert registry.get_handlers("test.item.created") == []
        assert registry.event_codes == frozenset()

    def test_get_handlers_returns_copy(self) -> None:
        """get_handlers 返回列表副本，修改不影响内部状态。"""
        handler_def = EventHandlerDefinition(
            code="h1",
            event_code="e1",
            description="d",
        )
        module = _make_module(event_handlers=frozenset({handler_def}))
        registry = _make_registry([module], {"h1": _noop_handler})

        handlers = registry.get_handlers("e1")
        handlers.clear()
        assert len(registry.get_handlers("e1")) == 1

    def test_handlers_sorted_by_code(self) -> None:
        """同一事件的多个处理器按编码稳定排序。"""
        defs = frozenset(
            {
                EventHandlerDefinition(code="c_handler", event_code="e1", description="c"),
                EventHandlerDefinition(code="a_handler", event_code="e1", description="a"),
                EventHandlerDefinition(code="b_handler", event_code="e1", description="b"),
            }
        )
        module = _make_module(event_handlers=defs)
        registry = _make_registry(
            [module],
            {
                "c_handler": _noop_handler,
                "a_handler": _noop_handler,
                "b_handler": _noop_handler,
            },
        )

        handlers = registry.get_handlers("e1")
        codes = [h.code for h in handlers]
        assert codes == ["a_handler", "b_handler", "c_handler"]


class TestEventHandlerRegistryValidation:
    """处理器注册表声明与实现校验（SPEC §5.7）。"""

    def test_missing_implementation_raises(self) -> None:
        """声明了事务内处理器但未提供实现 → 启动失败。"""
        handler_def = EventHandlerDefinition(
            code="audit.on_created",
            event_code="test.item.created",
            description="创建审计",
        )
        module = _make_module(event_handlers=frozenset({handler_def}))

        with pytest.raises(EventHandlerRegistrationError, match="未提供实现函数"):
            _make_registry([module], {})

    def test_undeclared_implementation_raises(self) -> None:
        """提供了实现但未声明 → 启动失败。"""
        module = _make_module()

        with pytest.raises(EventHandlerRegistrationError, match="未在 ModuleDefinition 中声明"):
            _make_registry([module], {"unknown.handler": _noop_handler})

    def test_non_transactional_impl_treated_as_undeclared(self) -> None:
        """为非事务内处理器提供的实现被视为未声明。"""
        handler_def = EventHandlerDefinition(
            code="ext.on_created",
            event_code="test.item.created",
            description="事务后",
            transactional=False,
        )
        module = _make_module(event_handlers=frozenset({handler_def}))

        with pytest.raises(EventHandlerRegistrationError, match="未在 ModuleDefinition 中声明"):
            _make_registry([module], {"ext.on_created": _noop_handler})


class TestDuplicateDetectionViaModuleRegistry:
    """重复事件编码或处理器编码 → 启动失败（SPEC §5.5、§5.7）。

    重复检测在 ModuleRegistry 中执行（TASK-006），此处验证事件系统
    依赖的 ModuleRegistry 正确检测重复。
    """

    def test_duplicate_event_code_raises(self) -> None:
        """两个模块声明相同事件编码 → ModuleRegistrationError。"""
        from app.modules.registry import ModuleRegistrationError

        event = EventDefinition(code="shared.event", description="共享事件")
        module_a = _make_module("A", events=frozenset({event}))
        module_b = _make_module("B", events=frozenset({event}))

        with pytest.raises(ModuleRegistrationError, match="重复事件"):
            ModuleRegistry([module_a, module_b])

    def test_duplicate_handler_code_raises(self) -> None:
        """两个模块声明相同处理器编码 → ModuleRegistrationError。"""
        from app.modules.registry import ModuleRegistrationError

        handler = EventHandlerDefinition(
            code="shared.handler",
            event_code="some.event",
            description="共享处理器",
        )
        module_a = _make_module("A", event_handlers=frozenset({handler}))
        module_b = _make_module("B", event_handlers=frozenset({handler}))

        with pytest.raises(ModuleRegistrationError, match="重复事件处理器"):
            ModuleRegistry([module_a, module_b])


# ---------------------------------------------------------------------------
# TransactionalEventDispatcher 测试（验收条件 #2、#4、#5）
# ---------------------------------------------------------------------------


class TestDispatcherCollect:
    """事件收集行为。"""

    def test_collect_adds_to_pending(self) -> None:
        """collect 将事件加入待处理列表。"""
        dispatcher = _make_dispatcher(frozenset(), {})
        event = _ItemCreated(occurred_at=_NOW, item_id="i-1", item_code="C")
        dispatcher.collect(event)
        assert dispatcher.pending_count == 1

    def test_collect_multiple_events(self) -> None:
        """可收集多个事件。"""
        dispatcher = _make_dispatcher(frozenset(), {})
        dispatcher.collect(_ItemCreated(occurred_at=_NOW, item_id="i-1", item_code="C"))
        dispatcher.collect(_ItemUpdated(occurred_at=_NOW, item_id="i-1", new_name="N"))
        assert dispatcher.pending_count == 2

    def test_no_pending_initially(self) -> None:
        """初始状态无待处理事件。"""
        dispatcher = _make_dispatcher(frozenset(), {})
        assert dispatcher.pending_count == 0


class TestDispatcherFlush:
    """事务内处理器在提交前执行（SPEC §5.7）。"""

    async def test_flush_executes_handlers(self) -> None:
        """flush 调用已注册的处理器。"""
        calls: list[str] = []

        async def handler(event: DomainEvent, uow: UnitOfWork) -> None:
            calls.append(event.code)

        handler_def = EventHandlerDefinition(
            code="h1",
            event_code="test.item.created",
            description="d",
        )
        dispatcher = _make_dispatcher(frozenset({handler_def}), {"h1": handler})
        dispatcher.collect(_ItemCreated(occurred_at=_NOW, item_id="i-1", item_code="C"))

        await dispatcher.flush(_FakeUow())
        assert calls == ["test.item.created"]

    async def test_flush_clears_pending(self) -> None:
        """flush 后待处理列表清空。"""
        handler_def = EventHandlerDefinition(
            code="h1",
            event_code="test.item.created",
            description="d",
        )
        dispatcher = _make_dispatcher(frozenset({handler_def}), {"h1": _noop_handler})
        dispatcher.collect(_ItemCreated(occurred_at=_NOW, item_id="i-1", item_code="C"))

        await dispatcher.flush(_FakeUow())
        assert dispatcher.pending_count == 0

    async def test_flush_with_no_events_is_noop(self) -> None:
        """无待处理事件时 flush 不执行任何操作。"""
        dispatcher = _make_dispatcher(frozenset(), {})
        await dispatcher.flush(_FakeUow())
        assert dispatcher.pending_count == 0

    async def test_flush_dispatches_events_in_fifo_order(self) -> None:
        """事件按收集顺序（FIFO）调度。"""
        order: list[str] = []

        async def handler_created(event: DomainEvent, uow: UnitOfWork) -> None:
            order.append("created")

        async def handler_updated(event: DomainEvent, uow: UnitOfWork) -> None:
            order.append("updated")

        defs = frozenset(
            {
                EventHandlerDefinition(
                    code="h_created", event_code="test.item.created", description=""
                ),
                EventHandlerDefinition(
                    code="h_updated", event_code="test.item.updated", description=""
                ),
            }
        )
        dispatcher = _make_dispatcher(
            defs,
            {"h_created": handler_created, "h_updated": handler_updated},
        )

        # 先收集 updated，再收集 created → 执行顺序应为 updated, created
        dispatcher.collect(_ItemUpdated(occurred_at=_NOW, item_id="i-1", new_name="N"))
        dispatcher.collect(_ItemCreated(occurred_at=_NOW, item_id="i-1", item_code="C"))

        await dispatcher.flush(_FakeUow())
        assert order == ["updated", "created"]

    async def test_handler_failure_raises_and_stops_dispatch(self) -> None:
        """任一处理器失败时抛出异常，后续处理器不执行（SPEC §5.7）。"""
        executed: list[str] = []

        async def handler_ok(event: DomainEvent, uow: UnitOfWork) -> None:
            executed.append("ok")

        async def handler_fail(event: DomainEvent, uow: UnitOfWork) -> None:
            executed.append("fail")
            raise RuntimeError("处理器失败")

        defs = frozenset(
            {
                EventHandlerDefinition(code="a_ok", event_code="e1", description=""),
                EventHandlerDefinition(code="b_fail", event_code="e1", description=""),
            }
        )
        dispatcher = _make_dispatcher(
            defs,
            {"a_ok": handler_ok, "b_fail": handler_fail},
        )

        @dataclass(frozen=True)
        class _E1Event(DomainEvent):
            code: ClassVar[str] = "e1"
            val: str

        dispatcher.collect(_E1Event(occurred_at=_NOW, val="x"))

        with pytest.raises(RuntimeError, match="处理器失败"):
            await dispatcher.flush(_FakeUow())

        # a_ok 先执行（按编码排序），b_fail 后执行并失败
        assert executed == ["ok", "fail"]

    async def test_multiple_handlers_sorted_by_code(self) -> None:
        """同一事件的多个处理器按编码稳定排序执行。"""
        order: list[str] = []

        def make_handler(name: str) -> TransactionalEventHandlerFn:
            async def h(event: DomainEvent, uow: UnitOfWork) -> None:
                order.append(name)

            return h

        defs = frozenset(
            {
                EventHandlerDefinition(code="c", event_code="e1", description=""),
                EventHandlerDefinition(code="a", event_code="e1", description=""),
                EventHandlerDefinition(code="b", event_code="e1", description=""),
            }
        )
        dispatcher = _make_dispatcher(
            defs,
            {"a": make_handler("a"), "b": make_handler("b"), "c": make_handler("c")},
        )

        @dataclass(frozen=True)
        class _E1Event(DomainEvent):
            code: ClassVar[str] = "e1"
            val: str

        dispatcher.collect(_E1Event(occurred_at=_NOW, val="x"))

        await dispatcher.flush(_FakeUow())
        assert order == ["a", "b", "c"]

    async def test_handler_receives_event_and_uow(self) -> None:
        """处理器接收到正确的事件实例和 Unit of Work。"""
        received_events: list[DomainEvent] = []
        received_uows: list[UnitOfWork] = []

        async def handler(event: DomainEvent, uow: UnitOfWork) -> None:
            received_events.append(event)
            received_uows.append(uow)

        handler_def = EventHandlerDefinition(
            code="h1",
            event_code="test.item.created",
            description="d",
        )
        dispatcher = _make_dispatcher(frozenset({handler_def}), {"h1": handler})
        event = _ItemCreated(occurred_at=_NOW, item_id="i-42", item_code="CODE")
        dispatcher.collect(event)

        uow = _FakeUow()
        await dispatcher.flush(uow)
        assert received_events == [event]
        assert received_uows == [uow]

    async def test_event_with_no_handlers_skipped(self) -> None:
        """无处理器的事件被跳过，不报错。"""
        dispatcher = _make_dispatcher(frozenset(), {})

        event = DomainEvent(occurred_at=_NOW)
        dispatcher.collect(event)

        await dispatcher.flush(_FakeUow())
        assert dispatcher.pending_count == 0
