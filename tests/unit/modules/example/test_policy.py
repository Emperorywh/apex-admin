"""示例模块领域策略单元测试（SPEC §30.2）。

测试名称校验策略的全部边界条件。策略不依赖应用层异常或基础设施。
"""

from __future__ import annotations

import pytest

from app.modules.example.domain.policy import ExampleNamePolicy

pytestmark = [pytest.mark.unit, pytest.mark.g1]


class TestExampleNamePolicy:
    """示例名称校验策略测试。"""

    def test_valid_name_passes(self) -> None:
        """合规名称通过校验。"""
        ExampleNamePolicy.validate("hello")
        ExampleNamePolicy.validate("a")
        ExampleNamePolicy.validate("x" * ExampleNamePolicy.MAX_NAME_LENGTH)

    def test_empty_string_raises(self) -> None:
        """空字符串抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能为空"):
            ExampleNamePolicy.validate("")

    def test_whitespace_only_raises(self) -> None:
        """纯空白字符串抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能为空"):
            ExampleNamePolicy.validate("   ")

    def test_too_long_raises(self) -> None:
        """超长名称抛出 ValueError。"""
        long_name = "x" * (ExampleNamePolicy.MAX_NAME_LENGTH + 1)
        with pytest.raises(ValueError, match="不能超过"):
            ExampleNamePolicy.validate(long_name)

    def test_boundary_length_passes(self) -> None:
        """恰好最大长度的名称通过校验。"""
        boundary_name = "x" * ExampleNamePolicy.MAX_NAME_LENGTH
        ExampleNamePolicy.validate(boundary_name)
