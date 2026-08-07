"""示例模块领域事件单元测试（SPEC §5.7、§30.2）。

测试事件的不变性、载荷类型校验和编码稳定性。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest

from app.events.base import DomainEvent
from app.modules.example.domain.events import ExampleItemCreated

pytestmark = [pytest.mark.unit, pytest.mark.g1]


class TestExampleItemCreated:
    """示例项目创建事件测试。"""

    def test_event_code_is_stable(self) -> None:
        """事件编码稳定且匹配模块声明。"""
        assert ExampleItemCreated.code == "example.item.created"

    def test_event_carries_payload(self) -> None:
        """事件正确携带 item_id 和 name 载荷。"""
        item_id = uuid4()
        now = datetime.now(UTC)
        event = ExampleItemCreated(
            occurred_at=now,
            item_id=item_id,
            name="test",
        )
        assert event.item_id == item_id
        assert event.name == "test"
        assert event.occurred_at == now

    def test_event_is_frozen(self) -> None:
        """事件不可变（SPEC §5.7）。"""
        from dataclasses import FrozenInstanceError

        now = datetime.now(UTC)
        event = ExampleItemCreated(
            occurred_at=now,
            item_id=uuid4(),
            name="test",
        )
        with pytest.raises(FrozenInstanceError):
            event.name = "changed"  # type: ignore[misc]

    def test_event_rejects_mutable_payload(self) -> None:
        """事件载荷拒绝可变容器（SPEC §5.7）。"""

        @dataclass(frozen=True)
        class _BadEvent(DomainEvent):
            code: ClassVar[str] = "test.bad"
            items: list[str]  # list 不被允许

        now = datetime.now(UTC)
        with pytest.raises(TypeError, match="不允许的类型"):
            _BadEvent(occurred_at=now, items=[1, 2, 3])  # type: ignore[arg-type]
