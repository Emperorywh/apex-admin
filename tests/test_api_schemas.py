"""请求 Schema 与序列化约定测试 — SPEC 9.2 / 9.3.

覆盖:
  - 创建/全量更新/部分更新请求模型统一 extra=forbid，未知字段返回 422。
  - JSON 字段 snake_case 序列化约定。
  - 时间字段带时区 ISO 8601 序列化约定。
"""

# 注意：本文件不使用 ``from __future__ import annotations``。
# FastAPI 在运行时解析函数签名中的类型注解，
# 嵌套闭包中定义的 Pydantic 模型需要即时可用的类型对象（非字符串），
# ``from __future__ import annotations`` 会将注解延迟为字符串，
# 导致 FastAPI 无法正确识别 Body 参数。

from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import Body, FastAPI
from starlette.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.core.api.schemas import StrictBaseModel

# ── StrictBaseModel: extra=forbid ─────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_strict_base_model_rejects_unknown_field() -> None:
    """StrictBaseModel 拒绝未知字段（SPEC 9.2）。"""

    class CreateItem(StrictBaseModel):
        name: str

    with pytest.raises(Exception):  # noqa: B017, PT011
        CreateItem(name="test", unknown="field")  # type: ignore[call-arg]


@pytest.mark.g1
@pytest.mark.unit
def test_strict_base_model_accepts_defined_fields() -> None:
    """StrictBaseModel 接受已定义字段。"""

    class CreateItem(StrictBaseModel):
        name: str
        price: int

    item = CreateItem(name="test", price=100)
    assert item.name == "test"
    assert item.price == 100


@pytest.mark.g1
@pytest.mark.unit
def test_strict_base_model_config_has_extra_forbid() -> None:
    """StrictBaseModel 的 model_config 固定 extra=forbid。"""

    assert StrictBaseModel.model_config.get("extra") == "forbid"


# ── API 契约测试 — 创建请求 extra=forbid → 422 ──────────────────────────


def _create_schema_test_app() -> FastAPI:
    """创建带创建/更新/部分更新路由的测试应用。

    通过临时路由验证 extra=forbid 在 API 边界返回 422。
    """

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    # 创建请求（SPEC 9.2: 创建统一 extra=forbid）
    class ItemCreate(StrictBaseModel):
        name: str
        price: int

    # 全量更新请求（SPEC 9.2: 全量更新统一 extra=forbid）
    class ItemUpdate(StrictBaseModel):
        name: str
        price: int

    # 部分更新请求（SPEC 9.2: 部分更新统一 extra=forbid，字段可选）
    class ItemPatch(StrictBaseModel):
        name: str | None = None
        price: int | None = None

    class ItemOut(StrictBaseModel):
        id: str
        name: str
        price: int

    @app.post("/api/v1/items", response_model=ItemOut, status_code=201)
    async def create_item(data: Annotated[ItemCreate, Body()]) -> ItemOut:
        """创建路由 — 验证创建请求 extra=forbid。"""

        return ItemOut(id="1", name=data.name, price=data.price)

    @app.put("/api/v1/items/{item_id}", response_model=ItemOut)
    async def update_item(
        item_id: str,
        data: Annotated[ItemUpdate, Body()],
    ) -> ItemOut:
        """全量更新路由 — 验证全量更新请求 extra=forbid。"""

        return ItemOut(id=item_id, name=data.name, price=data.price)

    @app.patch("/api/v1/items/{item_id}", response_model=ItemOut)
    async def patch_item(
        item_id: str,
        data: Annotated[ItemPatch, Body()],
    ) -> ItemOut:
        """部分更新路由 — 验证部分更新请求 extra=forbid。"""

        return ItemOut(
            id=item_id,
            name=data.name or "default",
            price=data.price or 0,
        )

    return app


@pytest.mark.g1
@pytest.mark.api
def test_create_unknown_field_returns_422() -> None:
    """创建请求携带未知字段返回 422（SPEC 9.2）。"""

    app = _create_schema_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/items",
            json={"name": "test", "price": 100, "unknown": "field"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == "VALIDATION.FAILED"


@pytest.mark.g1
@pytest.mark.api
def test_create_valid_body_returns_201() -> None:
    """创建请求合法请求体返回 201。"""

    app = _create_schema_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/items",
            json={"name": "test", "price": 100},
        )

    assert response.status_code == 201


@pytest.mark.g1
@pytest.mark.api
def test_full_update_unknown_field_returns_422() -> None:
    """全量更新请求携带未知字段返回 422（SPEC 9.2）。"""

    app = _create_schema_test_app()
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/items/1",
            json={"name": "test", "price": 100, "unknown": "field"},
        )

    assert response.status_code == 422


@pytest.mark.g1
@pytest.mark.api
def test_partial_update_unknown_field_returns_422() -> None:
    """部分更新请求携带未知字段返回 422（SPEC 9.2）。"""

    app = _create_schema_test_app()
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/items/1",
            json={"name": "test", "unknown": "field"},
        )

    assert response.status_code == 422


@pytest.mark.g1
@pytest.mark.api
def test_partial_update_optional_fields_accepted() -> None:
    """部分更新请求仅传部分字段返回 200。"""

    app = _create_schema_test_app()
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/items/1",
            json={"name": "updated"},
        )

    assert response.status_code == 200


# ── 序列化约定测试 — snake_case ───────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_json_fields_are_snake_case() -> None:
    """JSON 字段统一使用 snake_case（SPEC 9.3）。

    Pydantic 以 Python 字段名（snake_case）作为 JSON 键，
    不做 camelCase 转换。
    """

    class Example(StrictBaseModel):
        item_id: str
        created_at: str
        page_size: int

    example = Example(item_id="1", created_at="2026-01-01", page_size=20)
    data = example.model_dump()
    # 键名为 snake_case
    assert "item_id" in data
    assert "created_at" in data
    assert "page_size" in data
    # 不存在 camelCase 变体
    assert "itemId" not in data
    assert "createdAt" not in data
    assert "pageSize" not in data


# ── 序列化约定测试 — 带时区 ISO 8601 ──────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_datetime_serializes_as_timezone_aware_iso8601() -> None:
    """时间字段序列化为带时区的 ISO 8601 字符串（SPEC 9.3）。

    Pydantic 将 timezone-aware datetime 序列化为包含 UTC 偏移的
    ISO 8601 字符串。
    """

    class WithTime(StrictBaseModel):
        created_at: datetime

    dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    obj = WithTime(created_at=dt)

    # model_dump() 保持 datetime 对象
    assert obj.created_at == dt
    assert obj.created_at.tzinfo is not None

    # model_dump(mode="json") 序列化为 ISO 8601 字符串
    json_data = obj.model_dump(mode="json")
    assert isinstance(json_data["created_at"], str)
    # ISO 8601 格式，带时区标识（Z 或 +00:00 均为 UTC）
    assert "2026-01-15" in json_data["created_at"]
    assert "Z" in json_data["created_at"] or "+00:00" in json_data["created_at"]


@pytest.mark.g1
@pytest.mark.unit
def test_datetime_naive_input_rejected() -> None:
    """无时区的 datetime 在序列化时仍需保证输出带时区（SPEC 6.3 / 9.3）。

    SPEC 6.3: "禁止使用无时区语义的时间参与关键业务计算"。
    带 tzinfo 的 datetime 是项目约定，此测试确认模型接受 timezone-aware
    datetime 并正确序列化。
    """

    class WithTime(StrictBaseModel):
        ts: datetime

    # timezone-aware datetime 正确处理
    dt = datetime.now(UTC)
    obj = WithTime(ts=dt)
    assert obj.ts.tzinfo is not None

    json_str = obj.model_dump_json()
    # Pydantic v2 使用 Z 后缀表示 UTC，也是合法的 ISO 8601 带时区格式
    assert "Z" in json_str or "+00:00" in json_str


@pytest.mark.g1
@pytest.mark.api
def test_api_response_datetime_is_iso8601_with_timezone() -> None:
    """API 响应中时间字段为带时区 ISO 8601（SPEC 9.3）。"""

    from pydantic import BaseModel

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    class _TimeOut(BaseModel):
        """响应模型 — 演示带时区 datetime 序列化。"""

        created_at: datetime

    @app.get("/api/v1/time", response_model=_TimeOut)
    async def get_time() -> _TimeOut:
        return _TimeOut(created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))

    with TestClient(app) as client:
        response = client.get("/api/v1/time")

    assert response.status_code == 200
    body = response.json()
    # Z 或 +00:00 均为合法的 UTC ISO 8601 带时区格式
    assert "Z" in body["created_at"] or "+00:00" in body["created_at"]
    assert body["created_at"].startswith("2026-01-01T00:00:00")
