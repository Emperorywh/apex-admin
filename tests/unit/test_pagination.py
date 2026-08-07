"""分页与排序单元测试（SPEC §9.4、§28.1）。

覆盖验收条件：
- 分页参数 page（最小 1）、page_size（1–100）
- 分页响应 {items, total, page, page_size, pages}
- 排序参数：逗号分隔字段，- 前缀降序
- 排序字段使用每查询显式声明的白名单，不在白名单内返回参数错误
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    PaginationParams,
    paginate,
)
from app.api.sorting import SortInstruction, parse_sort
from app.errors.base import ParameterError

pytestmark = [pytest.mark.unit, pytest.mark.g1]


# ---------------------------------------------------------------------------
# PaginationParams 约束（SPEC §9.4）
# ---------------------------------------------------------------------------


class TestPaginationParamsDefaults:
    """验证分页参数默认值。"""

    def test_default_values(self) -> None:
        """默认 page=1，page_size=20（SPEC §9.4）。"""
        params = PaginationParams()
        assert params.page == 1
        assert params.page_size == DEFAULT_PAGE_SIZE
        assert DEFAULT_PAGE_SIZE == 20

    def test_max_page_size_is_100(self) -> None:
        """page_size 上限为 100（SPEC §9.4）。"""
        assert MAX_PAGE_SIZE == 100


class TestPaginationParamsConstraints:
    """验证分页参数范围约束。"""

    @pytest.mark.parametrize(
        ("page", "page_size"),
        [
            (1, 1),
            (1, 20),
            (1, 100),
            (10, 50),
            (999, 1),
        ],
    )
    def test_valid_values(self, page: int, page_size: int) -> None:
        """合法范围内的 page 和 page_size 通过校验。"""
        params = PaginationParams(page=page, page_size=page_size)
        assert params.page == page
        assert params.page_size == page_size

    @pytest.mark.parametrize("page", [0, -1, -100])
    def test_page_below_minimum_rejected(self, page: int) -> None:
        """page 小于 1 时校验失败。"""
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(page=page)
        # Pydantic greater_than_equal 错误
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("page",) for e in errors)

    @pytest.mark.parametrize("page_size", [0, -1, 101, 200, 999])
    def test_page_size_out_of_range_rejected(self, page_size: int) -> None:
        """page_size 小于 1 或大于 100 时校验失败。"""
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(page_size=page_size)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("page_size",) for e in errors)


class TestPaginationParamsExtraForbid:
    """验证 PaginationParams 拒绝未知字段。"""

    def test_extra_field_rejected(self) -> None:
        """PaginationParams 拒绝未知字段。"""
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(page=1, page_size=20, unknown="bad")  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        assert any(e["type"] == "extra_forbidden" for e in errors)


# ---------------------------------------------------------------------------
# Page 响应模型与 paginate 工具函数（SPEC §9.4）
# ---------------------------------------------------------------------------


class TestPageModel:
    """验证分页响应模型结构。"""

    def test_page_fields(self) -> None:
        """Page 包含 items、total、page、page_size、pages 字段。"""
        page = Page[dict[str, Any]](
            items=[{"id": 1}, {"id": 2}],
            total=100,
            page=1,
            page_size=20,
            pages=5,
        )
        assert page.items == [{"id": 1}, {"id": 2}]
        assert page.total == 100
        assert page.page == 1
        assert page.page_size == 20
        assert page.pages == 5

    def test_page_serializes_to_expected_json(self) -> None:
        """Page 序列化为 {items, total, page, page_size, pages}。"""
        page = Page[dict[str, Any]](
            items=[{"id": 1}],
            total=1,
            page=1,
            page_size=20,
            pages=1,
        )
        data = page.model_dump(mode="json")
        assert set(data.keys()) == {"items", "total", "page", "page_size", "pages"}

    def test_page_no_envelope(self) -> None:
        """Page 不包含 code/message/data 信封字段。"""
        page = Page[dict[str, Any]](
            items=[],
            total=0,
            page=1,
            page_size=20,
            pages=0,
        )
        data = page.model_dump(mode="json")
        assert "code" not in data
        assert "message" not in data
        assert "data" not in data


class TestPaginateFunction:
    """验证 paginate 工具函数。"""

    def test_paginate_normal(self) -> None:
        """正常分页：total=100, page_size=20 → pages=5。"""
        pagination = PaginationParams(page=1, page_size=20)
        result = paginate(items=[], total=100, pagination=pagination)
        assert result.total == 100
        assert result.page == 1
        assert result.page_size == 20
        assert result.pages == 5

    def test_paginate_uneven(self) -> None:
        """不整除分页：total=101, page_size=20 → pages=6。"""
        pagination = PaginationParams(page=1, page_size=20)
        result = paginate(items=[], total=101, pagination=pagination)
        assert result.pages == 6

    def test_paginate_zero_total(self) -> None:
        """total=0 → pages=0。"""
        pagination = PaginationParams(page=1, page_size=20)
        result = paginate(items=[], total=0, pagination=pagination)
        assert result.pages == 0
        assert result.items == []

    def test_paginate_preserves_items(self) -> None:
        """paginate 保留传入的 items 列表。"""
        pagination = PaginationParams(page=2, page_size=10)
        items = [
            {"id": 11, "name": "item-11"},
            {"id": 12, "name": "item-12"},
        ]
        result = paginate(items=items, total=50, pagination=pagination)
        assert result.items == items
        assert result.page == 2
        assert result.page_size == 10
        assert result.pages == 5

    def test_paginate_with_typed_items(self) -> None:
        """paginate 支持类型化 items（泛型 T）。"""

        class ItemResponse:
            pass

        pagination = PaginationParams(page=1, page_size=1)
        result: Page[dict[str, int]] = paginate(items=[{"id": 1}], total=1, pagination=pagination)
        assert result.items[0]["id"] == 1

    def test_paginate_max_page_size(self) -> None:
        """page_size=100 时正确计算页数。"""
        pagination = PaginationParams(page=1, page_size=100)
        result = paginate(items=[], total=250, pagination=pagination)
        assert result.pages == 3

    def test_paginate_page_size_one(self) -> None:
        """page_size=1 时 pages=total。"""
        pagination = PaginationParams(page=1, page_size=1)
        result = paginate(items=[], total=42, pagination=pagination)
        assert result.pages == 42


# ---------------------------------------------------------------------------
# 排序参数解析与白名单校验（SPEC §9.4）
# ---------------------------------------------------------------------------


class TestParseSort:
    """验证排序参数解析。"""

    def test_none_returns_empty(self) -> None:
        """sort=None 返回空列表。"""
        result = parse_sort(None, frozenset({"name"}))
        assert result == []

    def test_empty_string_returns_empty(self) -> None:
        """sort='' 返回空列表。"""
        result = parse_sort("", frozenset({"name"}))
        assert result == []

    def test_single_ascending(self) -> None:
        """单个升序字段正确解析。"""
        result = parse_sort("name", frozenset({"name", "created_at"}))
        assert result == [SortInstruction(field="name", descending=False)]

    def test_single_descending(self) -> None:
        """单个降序字段正确解析（- 前缀）。"""
        result = parse_sort("-created_at", frozenset({"name", "created_at"}))
        assert result == [SortInstruction(field="created_at", descending=True)]

    def test_multiple_fields(self) -> None:
        """逗号分隔多字段正确解析，保持顺序。"""
        result = parse_sort("-created_at,name", frozenset({"name", "created_at"}))
        assert result == [
            SortInstruction(field="created_at", descending=True),
            SortInstruction(field="name", descending=False),
        ]

    def test_mixed_directions(self) -> None:
        """混合升降序正确解析。"""
        result = parse_sort(
            "-priority,created_at,-name", frozenset({"name", "created_at", "priority"})
        )
        assert result == [
            SortInstruction(field="priority", descending=True),
            SortInstruction(field="created_at", descending=False),
            SortInstruction(field="name", descending=True),
        ]

    def test_whitespace_stripped(self) -> None:
        """字段两侧空白被去除。"""
        result = parse_sort(" name , -created_at ", frozenset({"name", "created_at"}))
        assert result == [
            SortInstruction(field="name", descending=False),
            SortInstruction(field="created_at", descending=True),
        ]

    def test_consecutive_commas_ignored(self) -> None:
        """连续逗号产生的空段被忽略。"""
        result = parse_sort("name,,created_at", frozenset({"name", "created_at"}))
        assert len(result) == 2

    def test_empty_segments_ignored(self) -> None:
        """仅含逗号和空白的字符串返回空列表。"""
        result = parse_sort(" , , ", frozenset({"name"}))
        assert result == []


class TestParseSortWhitelist:
    """验证排序字段白名单校验。"""

    def test_field_in_whitelist_accepted(self) -> None:
        """白名单内字段被接受。"""
        result = parse_sort("name", frozenset({"name"}))
        assert len(result) == 1

    def test_field_not_in_whitelist_rejected(self) -> None:
        """白名单外字段抛出 ParameterError。"""
        with pytest.raises(ParameterError) as exc_info:
            parse_sort("password", frozenset({"name", "created_at"}))
        assert "password" in str(exc_info.value)

    def test_multiple_fields_one_not_in_whitelist(self) -> None:
        """多个字段中有一个不在白名单时抛出 ParameterError。"""
        with pytest.raises(ParameterError):
            parse_sort("name,evil_field", frozenset({"name"}))

    def test_empty_whitelist_rejects_all(self) -> None:
        """空白名单时任何字段都被拒绝。"""
        with pytest.raises(ParameterError):
            parse_sort("name", frozenset())

    def test_descending_field_not_in_whitelist_rejected(self) -> None:
        """降序字段不在白名单时也抛出 ParameterError。"""
        with pytest.raises(ParameterError):
            parse_sort("-evil", frozenset({"name"}))

    def test_error_code_is_app_parameter(self) -> None:
        """ParameterError 使用 APP.PARAMETER 错误码。"""
        with pytest.raises(ParameterError) as exc_info:
            parse_sort("evil", frozenset({"name"}))
        assert exc_info.value.code == "APP.PARAMETER"
        assert exc_info.value.http_status == 400
