"""示例模块 API 契约测试 — SPEC 9.3 / 28.4.

覆盖验收标准:
  - AC-1: CRUD 与分页排序 API 契约测试通过（真实 PostgreSQL）。
  - AC-4: OpenAPI 快照包含示例路由且 operationId 唯一。

SPEC 28.4: 测试 HTTP 状态码、分页排序、RFC 9457 错误结构。

使用 TestClient 对真实应用发请求，连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.composition.modules import MODULE_VERSION_LOCATIONS
from app.core.config import Environment, Settings
from app.infrastructure.db.engine import create_db_engine
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator


# ── 测试 fixture ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移，确保 example_items 表存在。

    模块级 fixture：同一模块内所有测试共享迁移后的数据库。
    """

    from alembic import command

    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    asyncio.run(asyncio.to_thread(lambda: command.upgrade(config, "head")))

    yield database_url

    # 清理数据
    engine = create_db_engine(database_url)
    try:

        async def _cleanup() -> None:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM example_items"))

        asyncio.run(_cleanup())
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clean_items(migrated_database_url: str) -> Iterator[None]:
    """每个测试前清理 example_items 表，确保测试间无数据残留。"""

    engine = create_db_engine(migrated_database_url)
    asyncio.run(_async_clean(engine))
    yield
    asyncio.run(_async_clean(engine))
    asyncio.run(engine.dispose())


async def _async_clean(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM example_items"))


@pytest.fixture()
def api_client(migrated_database_url: str) -> Iterator[TestClient]:
    """创建使用迁移后数据库的 TestClient。"""

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=migrated_database_url,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


# ── CRUD 测试 ─────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.api
def test_create_item_returns_201(api_client: TestClient) -> None:
    """创建条目返回 HTTP 201（SPEC 9.3）。"""

    response = api_client.post(
        "/api/v1/example/items",
        json={"name": "api-test", "description": "via API"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "api-test"
    assert "id" in body
    assert "created_at" in body

    # 事件处理器在事务内更新了数据库行（SPEC 5.7），
    # 通过后续 GET 验证事件处理器效果
    get_resp = api_client.get(f"/api/v1/example/items/{body['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["description"] == "[processed]"


@pytest.mark.g1
@pytest.mark.api
def test_get_item_returns_200(api_client: TestClient) -> None:
    """查询条目返回资源 Schema（SPEC 9.3）。"""

    # 先创建
    create_resp = api_client.post(
        "/api/v1/example/items",
        json={"name": "get-test"},
    )
    assert create_resp.status_code == 201
    item_id = create_resp.json()["id"]

    # 再查询
    response = api_client.get(f"/api/v1/example/items/{item_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == item_id
    assert body["name"] == "get-test"


@pytest.mark.g1
@pytest.mark.api
def test_get_nonexistent_returns_404(api_client: TestClient) -> None:
    """查询不存在的条目返回 404 problem+json（SPEC 9.3 / 10.1）。"""

    response = api_client.get(
        "/api/v1/example/items/00000000-0000-0000-0000-000000000000",
    )
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "EXAMPLE.NOT_FOUND"
    assert body["type"] == "urn:apex:problem:example.not_found"


@pytest.mark.g1
@pytest.mark.api
def test_list_items_pagination(api_client: TestClient) -> None:
    """分页查询 — SPEC 9.4 响应结构。"""

    # 创建多条
    for i in range(3):
        api_client.post(
            "/api/v1/example/items",
            json={"name": f"list-item-{i}"},
        )

    response = api_client.get(
        "/api/v1/example/items",
        params={"page": 1, "page_size": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 2


@pytest.mark.g1
@pytest.mark.api
def test_list_items_sorting(api_client: TestClient) -> None:
    """排序查询 — SPEC 9.4 排序白名单。"""

    # 创建多条（名称倒序，验证排序效果）
    for name in ["charlie", "alpha", "bravo"]:
        api_client.post("/api/v1/example/items", json={"name": name})

    # 按名称升序
    response = api_client.get(
        "/api/v1/example/items",
        params={"sort": "name", "page_size": 10},
    )
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert names == ["alpha", "bravo", "charlie"]

    # 按名称降序
    response = api_client.get(
        "/api/v1/example/items",
        params={"sort": "-name", "page_size": 10},
    )
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert names == ["charlie", "bravo", "alpha"]


@pytest.mark.g1
@pytest.mark.api
def test_invalid_sort_field_returns_400(api_client: TestClient) -> None:
    """非白名单排序字段返回 400（SPEC 9.4 / 23.3）。"""

    response = api_client.get(
        "/api/v1/example/items",
        params={"sort": "malicious_field"},
    )
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.g1
@pytest.mark.api
def test_create_duplicate_returns_409(api_client: TestClient) -> None:
    """重复名称返回 409 problem+json（SPEC 9.3 / 10.1）。"""

    api_client.post("/api/v1/example/items", json={"name": "duplicate"})

    response = api_client.post(
        "/api/v1/example/items",
        json={"name": "duplicate"},
    )
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "EXAMPLE.CONFLICT"


@pytest.mark.g1
@pytest.mark.api
def test_create_unknown_field_returns_422(api_client: TestClient) -> None:
    """未知字段返回 422（SPEC 9.2: extra="forbid"）。"""

    response = api_client.post(
        "/api/v1/example/items",
        json={"name": "test", "unknown_field": "value"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert "errors" in body


@pytest.mark.g1
@pytest.mark.api
def test_update_item(api_client: TestClient) -> None:
    """更新条目 — PUT 全量更新（SPEC 9.2）。"""

    create_resp = api_client.post(
        "/api/v1/example/items",
        json={"name": "original"},
    )
    item_id = create_resp.json()["id"]

    response = api_client.put(
        f"/api/v1/example/items/{item_id}",
        json={"name": "updated", "description": "new desc"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "updated"
    assert body["description"] == "new desc"


@pytest.mark.g1
@pytest.mark.api
def test_delete_item_returns_204(api_client: TestClient) -> None:
    """删除条目返回 204（SPEC 9.3）。"""

    create_resp = api_client.post(
        "/api/v1/example/items",
        json={"name": "to-delete"},
    )
    item_id = create_resp.json()["id"]

    response = api_client.delete(f"/api/v1/example/items/{item_id}")
    assert response.status_code == 204

    # 确认已删除
    get_resp = api_client.get(f"/api/v1/example/items/{item_id}")
    assert get_resp.status_code == 404


@pytest.mark.g1
@pytest.mark.api
def test_delete_nonexistent_returns_404(api_client: TestClient) -> None:
    """删除不存在的条目返回 404（SPEC 9.3 / 10.1）。"""

    response = api_client.delete(
        "/api/v1/example/items/00000000-0000-0000-0000-000000000000",
    )
    assert response.status_code == 404


# ── OpenAPI 路由验证 ──────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.api
def test_openapi_contains_example_routes(api_client: TestClient) -> None:
    """OpenAPI 包含示例路由（AC-4）。"""

    response = api_client.get("/openapi.json")
    schema = response.json()

    example_paths = [
        p for p in schema.get("paths", {}) if p.startswith("/api/v1/example/items")
    ]
    assert len(example_paths) >= 2  # /items and /items/{item_id}

    # 统计示例路由的 operation 数量（POST, GET list, GET {id}, PUT, DELETE）
    example_ops = 0
    for path, path_data in schema.get("paths", {}).items():
        if not path.startswith("/api/v1/example/items"):
            continue
        for method_data in path_data.values():
            if isinstance(method_data, dict) and "operationId" in method_data:
                example_ops += 1
    assert example_ops >= 4


@pytest.mark.g1
@pytest.mark.api
def test_openapi_example_operation_ids_unique(
    api_client: TestClient,
) -> None:
    """示例路由 operationId 全局唯一（AC-4 / SPEC 28.4）。"""

    response = api_client.get("/openapi.json")
    schema = response.json()

    operation_ids: list[str] = []
    for path_data in schema.get("paths", {}).values():
        for method_data in path_data.values():
            if isinstance(method_data, dict) and "operationId" in method_data:
                operation_ids.append(method_data["operationId"])

    # 全局唯一
    assert len(operation_ids) == len(set(operation_ids))

    # 包含示例路由的 operationId
    example_ops = [oid for oid in operation_ids if "example" in oid.lower()]
    assert len(example_ops) >= 4
