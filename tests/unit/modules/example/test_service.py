"""示例模块应用服务单元测试（SPEC §5.6、§5.7、§30.2）。

使用内存 Fake UoW 和 Repository 验证 Use Case 的编排逻辑、
领域策略调用、事件收集和事务提交/回滚行为。
不依赖数据库或 Docker。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from app.errors import ParameterError
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.example.application.port import (
    ExampleRepository,
    ExampleUnitOfWork,
)
from app.modules.example.application.service import ExampleService
from app.modules.example.domain.events import ExampleItemCreated
from app.modules.example.domain.model import ExampleItem
from app.modules.registry import ModuleRegistry

pytestmark = [pytest.mark.unit, pytest.mark.g1]


# ---------------------------------------------------------------------------
# 内存 Fake 实现（不依赖数据库）
# ---------------------------------------------------------------------------


class FakeExampleRepository(ExampleRepository):
    """内存 Repository，存储 ExampleItem。"""

    def __init__(self) -> None:
        self._items: dict[UUID, ExampleItem] = {}

    async def add(self, entity: ExampleItem) -> None:
        self._items[entity.id] = entity

    async def get_by_id(self, item_id: UUID) -> ExampleItem | None:
        return self._items.get(item_id)

    async def count(self) -> int:
        return len(self._items)

    async def list_paginated(self, offset: int, limit: int) -> list[ExampleItem]:
        all_items = sorted(self._items.values(), key=lambda i: i.created_at, reverse=True)
        return all_items[offset : offset + limit]


class FakeExampleUnitOfWork(ExampleUnitOfWork):
    """内存 UoW，记录提交/回滚状态。"""

    def __init__(self) -> None:
        self._repo = FakeExampleRepository()
        self.committed = False
        self.rolled_back = False
        self._active = False

    async def __aenter__(self) -> Self:
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._active = False
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    @property
    def examples(self) -> FakeExampleRepository:
        return self._repo


def _make_dispatcher() -> TransactionalEventDispatcher:
    """构造带空处理器注册表的事件调度器。"""
    empty_registry = EventHandlerRegistry(ModuleRegistry([]), {})
    return TransactionalEventDispatcher(empty_registry)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestCreateItem:
    """创建示例项目 Use Case 测试。"""

    async def test_create_item_success(self) -> None:
        """成功创建项目，事件被收集，UoW 提交。"""
        uow = FakeExampleUnitOfWork()
        dispatcher = _make_dispatcher()
        service = ExampleService(lambda: uow, dispatcher)

        now = datetime.now(UTC)
        item = await service.create_item(name="hello", current_time=now)

        assert item.name == "hello"
        assert item.created_at == now
        assert uow.committed is True
        assert uow.rolled_back is False
        # 事件已被 flush 清空
        assert dispatcher.pending_count == 0
        # 数据已存入 Repository
        assert await uow.examples.count() == 1

    async def test_create_item_invalid_name_raises_parameter_error(self) -> None:
        """不合规名称抛出 ParameterError（携带稳定错误码）。"""
        uow = FakeExampleUnitOfWork()
        service = ExampleService(lambda: uow, _make_dispatcher())

        with pytest.raises(ParameterError, match="EXAMPLE.INVALID_NAME"):
            await service.create_item(name="", current_time=datetime.now(UTC))

    async def test_create_item_invalid_name_does_not_commit(self) -> None:
        """校验失败时不提交。"""
        uow = FakeExampleUnitOfWork()
        service = ExampleService(lambda: uow, _make_dispatcher())

        with pytest.raises(ParameterError):
            await service.create_item(name="", current_time=datetime.now(UTC))

        assert uow.committed is False
        assert uow.rolled_back is True

    async def test_create_item_collects_event_before_flush(self) -> None:
        """创建项目时收集 ExampleItemCreated 事件。"""
        uow = FakeExampleUnitOfWork()
        dispatcher = _make_dispatcher()
        service = ExampleService(lambda: uow, dispatcher)

        now = datetime.now(UTC)
        await service.create_item(name="test", current_time=now)

        # flush 后 pending 清空（SPEC §5.7：flush 开始后清空待处理列表）
        assert dispatcher.pending_count == 0


class TestListItems:
    """查询示例项目列表 Use Case 测试。"""

    async def test_list_empty(self) -> None:
        """空库返回空列表和零总数。"""
        uow = FakeExampleUnitOfWork()
        service = ExampleService(lambda: uow, _make_dispatcher())

        items, total = await service.list_items(page=1, page_size=20)
        assert items == []
        assert total == 0

    async def test_list_with_data(self) -> None:
        """有多条数据时返回正确分页。"""
        uow = FakeExampleUnitOfWork()
        now = datetime.now(UTC)
        # 预填充 3 条
        for i in range(3):
            await uow.examples.add(ExampleItem.new(name=f"item-{i}", created_at=now))

        service = ExampleService(lambda: uow, _make_dispatcher())
        items, total = await service.list_items(page=1, page_size=2)

        assert total == 3
        assert len(items) == 2

    async def test_list_second_page(self) -> None:
        """第二页返回剩余数据。"""
        uow = FakeExampleUnitOfWork()
        now = datetime.now(UTC)
        for i in range(3):
            await uow.examples.add(ExampleItem.new(name=f"item-{i}", created_at=now))

        service = ExampleService(lambda: uow, _make_dispatcher())
        items, total = await service.list_items(page=2, page_size=2)

        assert total == 3
        assert len(items) == 1


class TestEventDispatch:
    """事件调度测试。"""

    async def test_event_handler_receives_event(self) -> None:
        """注册的处理器在 flush 时被调用。"""
        from app.modules.example.definition import MODULE

        received_events: list[ExampleItemCreated] = []

        async def capturing_handler(
            event: ExampleItemCreated,  # type: ignore[override]
            uow: object,
        ) -> None:
            if isinstance(event, ExampleItemCreated):
                received_events.append(event)

        registry = EventHandlerRegistry(
            ModuleRegistry([MODULE]),
            {"example.handler.item_created": capturing_handler},
        )
        dispatcher = TransactionalEventDispatcher(registry)

        uow = FakeExampleUnitOfWork()
        service = ExampleService(lambda: uow, dispatcher)

        now = datetime.now(UTC)
        await service.create_item(name="event-test", current_time=now)

        assert len(received_events) == 1
        assert received_events[0].name == "event-test"
