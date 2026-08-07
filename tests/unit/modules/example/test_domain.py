"""示例模块领域层单元测试（SPEC §30.2）。

测试领域实体的创建、属性和工厂方法。领域层不依赖任何基础设施。
"""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

import pytest

from app.modules.example.domain.model import ExampleItem

pytestmark = [pytest.mark.unit, pytest.mark.g1]


class TestExampleItem:
    """示例领域实体测试。"""

    def test_new_generates_uuid(self) -> None:
        """工厂方法生成有效的 UUID。"""
        from datetime import datetime

        item = ExampleItem.new(
            name="test",
            created_at=datetime.now(UTC),
        )
        assert isinstance(item.id, UUID)

    def test_new_assigns_name(self) -> None:
        """工厂方法正确赋值名称。"""
        from datetime import datetime

        now = datetime.now(UTC)
        item = ExampleItem.new(name="hello", created_at=now)
        assert item.name == "hello"

    def test_new_assigns_created_at(self) -> None:
        """工厂方法正确赋值创建时间。"""
        from datetime import datetime

        now = datetime.now(UTC)
        item = ExampleItem.new(name="hello", created_at=now)
        assert item.created_at == now

    def test_entity_is_frozen(self) -> None:
        """实体不可变。"""
        from dataclasses import FrozenInstanceError
        from datetime import datetime

        item = ExampleItem.new(
            name="test",
            created_at=datetime.now(UTC),
        )
        with pytest.raises(FrozenInstanceError):
            item.name = "changed"  # type: ignore[misc]

    def test_two_items_have_different_ids(self) -> None:
        """每次创建生成不同 UUID。"""
        from datetime import datetime

        now = datetime.now(UTC)
        item_a = ExampleItem.new(name="a", created_at=now)
        item_b = ExampleItem.new(name="b", created_at=now)
        assert item_a.id != item_b.id
