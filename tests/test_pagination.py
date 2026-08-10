"""分页与排序框架测试 — SPEC 9.4.

覆盖:
  - 分页默认值 page=1、page_size=20。
  - page 最小 1、page_size 范围 1-100，越界返回 422。
  - 响应固定 {items, total, page, page_size, pages}。
  - 排序解析：逗号分隔、``-`` 前缀降序、空白段忽略。
  - 排序白名单：非白名单字段返回参数错误 problem+json（400）。
  - total_pages 计算正确。
  - 游标分页禁混用规则有文档约束。
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from starlette.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.core.api.pagination import (
    PageParams,
    PageResponse,
    SortField,
    SortOrder,
    parse_sort,
    sort_dependency,
    total_pages,
)
from app.core.errors.exceptions import ParameterError

# ── PageParams 单元测试 ───────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_page_params_defaults() -> None:
    """分页参数默认值 page=1、page_size=20（SPEC 9.4）。"""

    params = PageParams()
    assert params.page == 1
    assert params.page_size == 20


@pytest.mark.g1
@pytest.mark.unit
def test_page_params_offset_calculation() -> None:
    """offset 计算正确（零基）。"""

    params = PageParams(page=3, page_size=10)
    assert params.offset == 20


@pytest.mark.g1
@pytest.mark.unit
def test_page_params_unpacking() -> None:
    """PageParams 支持 page, page_size 解包。"""

    params = PageParams(page=2, page_size=50)
    p, ps = params  # type: ignore[misc]
    assert p == 2
    assert ps == 50


# ── total_pages 单元测试 ──────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
@pytest.mark.parametrize(
    ("total", "page_size", "expected"),
    [
        (0, 20, 0),
        (1, 20, 1),
        (20, 20, 1),
        (21, 20, 2),
        (100, 20, 5),
        (101, 20, 6),
        (50, 0, 0),
        (50, -1, 0),
    ],
)
def test_total_pages(total: int, page_size: int, expected: int) -> None:
    """总页数计算正确。"""

    assert total_pages(total, page_size) == expected


# ── PageResponse 结构测试 ─────────────────────────────────────────────────


class _Item(BaseModel):
    """测试用分页项 Schema。"""

    id: str
    name: str


@pytest.mark.g1
@pytest.mark.unit
def test_page_response_fixed_fields() -> None:
    """PageResponse 固定包含 {items, total, page, page_size, pages}（SPEC 9.4）。"""

    response = PageResponse[_Item](
        items=[_Item(id="1", name="a")],
        total=100,
        page=1,
        page_size=20,
        pages=5,
    )
    data = response.model_dump()
    assert set(data.keys()) == {"items", "total", "page", "page_size", "pages"}
    assert data["total"] == 100
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["pages"] == 5
    assert len(data["items"]) == 1


@pytest.mark.g1
@pytest.mark.unit
def test_page_response_no_envelope() -> None:
    """分页响应不使用 {code, message, data} 信封（SPEC 9.3）。"""

    response = PageResponse[_Item](
        items=[],
        total=0,
        page=1,
        page_size=20,
        pages=0,
    )
    data = response.model_dump()
    assert "code" not in data
    assert "message" not in data
    assert "data" not in data


# ── parse_sort 单元测试 ───────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_none_returns_empty() -> None:
    """sort=None 返回空列表（不排序）。"""

    assert parse_sort(None, frozenset({"name"})) == []


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_empty_string_returns_empty() -> None:
    """sort 为空字符串或纯空白返回空列表。"""

    assert parse_sort("", frozenset({"name"})) == []
    assert parse_sort("  ", frozenset({"name"})) == []


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_single_ascending() -> None:
    """单个升序字段。"""

    result = parse_sort("name", frozenset({"name", "created_at"}))
    assert len(result) == 1
    assert result[0].name == "name"
    assert result[0].order == SortOrder.ASC


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_single_descending() -> None:
    """单个降序字段（- 前缀）。"""

    result = parse_sort("-created_at", frozenset({"name", "created_at"}))
    assert len(result) == 1
    assert result[0].name == "created_at"
    assert result[0].order == SortOrder.DESC


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_multiple_fields() -> None:
    """逗号分隔多字段，保持声明顺序。"""

    result = parse_sort("-created_at,name", frozenset({"name", "created_at"}))
    assert len(result) == 2
    assert result[0] == SortField(name="created_at", order=SortOrder.DESC)
    assert result[1] == SortField(name="name", order=SortOrder.ASC)


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_ignores_empty_segments() -> None:
    """连续逗号或前后空白被忽略。"""

    result = parse_sort("name,,created_at,", frozenset({"name", "created_at"}))
    assert len(result) == 2


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_rejects_non_whitelist_field() -> None:
    """非白名单字段抛出 ParameterError（SPEC 9.4）。"""

    with pytest.raises(ParameterError):
        parse_sort("hacker_field", frozenset({"name", "created_at"}))


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_rejects_non_whitelist_descending() -> None:
    """非白名单字段（带 - 前缀）抛出 ParameterError。"""

    with pytest.raises(ParameterError):
        parse_sort("-hacker_field", frozenset({"name", "created_at"}))


@pytest.mark.g1
@pytest.mark.unit
def test_parse_sort_rejects_mixed_whitelist_and_non() -> None:
    """白名单和非白名单字段混合时抛出 ParameterError。"""

    with pytest.raises(ParameterError):
        parse_sort("name,hacker_field", frozenset({"name", "created_at"}))


# ── API 契约测试 — 分页参数校验 ──────────────────────────────────────────


def _create_pagination_test_app() -> FastAPI:
    """创建带分页路由的测试应用。"""

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    class ItemOut(BaseModel):
        id: str
        name: str

    @app.get("/api/v1/items", response_model=PageResponse[ItemOut])
    async def list_items(
        params: Annotated[PageParams, Depends()],
    ) -> PageResponse[ItemOut]:
        """使用 PageParams 依赖的分页查询测试路由。"""

        return PageResponse[ItemOut](
            items=[ItemOut(id="1", name="a")],
            total=1,
            page=params.page,
            page_size=params.page_size,
            pages=1,
        )

    return app


@pytest.mark.g1
@pytest.mark.api
def test_api_pagination_defaults() -> None:
    """不带分页参数时使用默认值 page=1、page_size=20（SPEC 9.4）。"""

    app = _create_pagination_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items")

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert set(data.keys()) == {"items", "total", "page", "page_size", "pages"}


@pytest.mark.g1
@pytest.mark.api
def test_api_pagination_custom_values() -> None:
    """自定义分页参数正确传递。"""

    app = _create_pagination_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items?page=2&page_size=50")

    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2
    assert data["page_size"] == 50


@pytest.mark.g1
@pytest.mark.api
def test_api_pagination_page_below_minimum_returns_422() -> None:
    """page < 1 返回 422（SPEC 9.4）。"""

    app = _create_pagination_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items?page=0")

    assert response.status_code == 422


@pytest.mark.g1
@pytest.mark.api
def test_api_pagination_page_size_below_minimum_returns_422() -> None:
    """page_size < 1 返回 422（SPEC 9.4）。"""

    app = _create_pagination_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items?page_size=0")

    assert response.status_code == 422


@pytest.mark.g1
@pytest.mark.api
def test_api_pagination_page_size_above_maximum_returns_422() -> None:
    """page_size > 100 返回 422（SPEC 9.4）。"""

    app = _create_pagination_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items?page_size=101")

    assert response.status_code == 422


# ── API 契约测试 — 排序白名单校验 ────────────────────────────────────────


def _create_sort_test_app() -> FastAPI:
    """创建带排序路由的测试应用。"""

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.get("/api/v1/items")
    async def list_items(
        sort: Annotated[
            list[SortField],
            Depends(sort_dependency(frozenset({"name", "created_at"}))),
        ],
    ) -> dict[str, Any]:
        """使用 sort_dependency 的排序查询测试路由。"""

        return {
            "sort": [{"name": f.name, "order": f.order.value} for f in sort],
        }

    return app


@pytest.mark.g1
@pytest.mark.api
def test_api_sort_valid_field_returns_200() -> None:
    """白名单内排序字段返回 200。"""

    app = _create_sort_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items?sort=-created_at,name")

    assert response.status_code == 200
    data = response.json()
    assert len(data["sort"]) == 2
    assert data["sort"][0] == {"name": "created_at", "order": "desc"}
    assert data["sort"][1] == {"name": "name", "order": "asc"}


@pytest.mark.g1
@pytest.mark.api
def test_api_sort_no_param_returns_empty() -> None:
    """不带 sort 参数返回空排序。"""

    app = _create_sort_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items")

    assert response.status_code == 200
    assert response.json()["sort"] == []


@pytest.mark.g1
@pytest.mark.api
def test_api_sort_non_whitelist_returns_400_problem_json() -> None:
    """非白名单排序字段返回 400 problem+json（SPEC 9.4）。"""

    app = _create_sort_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items?sort=hacker_field")

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "PARAMETER.INVALID"
    assert data["type"] == "urn:apex:problem:parameter.invalid"


@pytest.mark.g1
@pytest.mark.api
def test_api_sort_non_whitelist_mixed_returns_400() -> None:
    """白名单与非白名单混合时返回 400。"""

    app = _create_sort_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/items?sort=name,hacker_field")

    assert response.status_code == 400
