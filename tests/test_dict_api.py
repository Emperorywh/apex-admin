"""数据字典模块 API 契约测试 — SPEC 17.1 / 17.2 / 28.4.

覆盖验收标准:
  - 字典类型与字典项 API 契约：创建/查询/更新/启用禁用。
  - 字典项含显示文本/稳定值/排序/扩展元数据。
  - 字典编码唯一冲突返回稳定冲突错误码。
  - 被引用的字典类型删除被拒绝。
  - 字典项变更写审计。

使用 TestClient 对真实应用发请求，覆盖认证/权限依赖以聚焦路由契约。
连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.application.context import UseCaseContext
from app.composition.modules import MODULE_VERSION_LOCATIONS
from app.core.config import Environment, Settings
from app.infrastructure.db.engine import create_db_engine
from app.main import create_app
from app.modules.auth.dependencies import get_authenticated_context_async
from app.modules.auth.permission import ActorAuthorization, get_actor_authorization

if TYPE_CHECKING:
    from collections.abc import Iterator


# ── 迁移与清理 ─────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from alembic import command

    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_tables(database_url: str) -> None:
    """清理字典与审计表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM dict_references"))
            await conn.execute(text("DELETE FROM dict_items"))
            await conn.execute(text("DELETE FROM dict_types"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


async def _seed_reference(database_url: str, dict_type_code: str) -> None:
    """在 dict_references 表中插入一条引用记录。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO dict_references "
                    "(id, dict_type_code, module_code, resource_id, "
                    "created_at) VALUES (:id, :code, 'identity', 'res-1', :t)",
                ),
                {
                    "id": str(uuid4()),
                    "code": dict_type_code,
                    "t": datetime.now(UTC),
                },
            )
    finally:
        await engine.dispose()


# ── 测试 fixture ───────────────────────────────────────────────────────────

_TEST_ACTOR_ID = "00000000-0000-0000-0000-0000000000dd"

_SUPER_ADMIN_CTX = UseCaseContext(
    request_id="test-dict-req",
    actor_id=_TEST_ACTOR_ID,
)


def _super_admin_auth_override() -> ActorAuthorization:
    """模拟超管授权。"""

    return ActorAuthorization(
        ctx=_SUPER_ADMIN_CTX,
        permissions=frozenset(),
        is_super_admin=True,
    )


def _super_admin_ctx_override() -> UseCaseContext:
    """模拟认证上下文。"""

    return _SUPER_ADMIN_CTX


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移。"""

    asyncio.run(_apply_migrations(database_url))
    yield database_url


@pytest.fixture(autouse=True)
def _clean_tables(migrated_database_url: str) -> Iterator[None]:
    """每个测试前后清理全部表。"""

    asyncio.run(_cleanup_tables(migrated_database_url))
    yield
    asyncio.run(_cleanup_tables(migrated_database_url))


@pytest.fixture()
def api_client(migrated_database_url: str) -> Iterator[TestClient]:
    """创建带字典模块和超管权限的 TestClient。"""

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=migrated_database_url,
    )
    app = create_app(settings)
    app.dependency_overrides[get_authenticated_context_async] = (
        _super_admin_ctx_override
    )
    app.dependency_overrides[get_actor_authorization] = _super_admin_auth_override
    with TestClient(app) as client:
        yield client


def _create_dict_type(
    client: TestClient,
    *,
    code: str = "test_type",
    name: str = "测试类型",
    description: str | None = None,
) -> dict[str, object]:
    """通过 API 创建字典类型并返回响应体。"""

    payload: dict[str, object] = {"code": code, "name": name}
    if description is not None:
        payload["description"] = description
    response = client.post("/api/v1/dict-types", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_dict_item(
    client: TestClient,
    type_id: str,
    *,
    label: str = "测试项",
    value: str = "test_val",
    sort_order: int = 0,
    metadata: dict[str, object] | None = None,
    description: str | None = None,
) -> dict[str, object]:
    """通过 API 创建字典项并返回响应体。"""

    payload: dict[str, object] = {
        "label": label,
        "value": value,
        "sort_order": sort_order,
    }
    if metadata is not None:
        payload["metadata"] = metadata
    if description is not None:
        payload["description"] = description
    response = client.post(
        f"/api/v1/dict-types/{type_id}/items",
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 字典类型 API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestDictTypeAPI:
    """字典类型 API 契约测试 — SPEC 17.1."""

    def test_create_dict_type_201_with_location(
        self,
        api_client: TestClient,
    ) -> None:
        """创建字典类型返回 201 + Location。"""

        response = api_client.post(
            "/api/v1/dict-types",
            json={"code": "api_gender", "name": "性别"},
        )
        assert response.status_code == 201
        assert "location" in {k.lower() for k in response.headers}
        body = response.json()
        assert body["code"] == "api_gender"
        assert body["name"] == "性别"
        assert body["status"] == "active"

    def test_create_duplicate_code_409_stable_error(
        self,
        api_client: TestClient,
    ) -> None:
        """字典编码冲突返回 409 稳定冲突错误码 — SPEC 17.1."""

        _create_dict_type(api_client, code="dup_api", name="重复")
        response = api_client.post(
            "/api/v1/dict-types",
            json={"code": "dup_api", "name": "再次"},
        )
        assert response.status_code == 409
        body = response.json()
        # SPEC: 稳定冲突错误码
        assert body["type"] == "urn:apex:problem:dict.type_duplicate_code"

    def test_list_dict_types(self, api_client: TestClient) -> None:
        """查询字典类型列表。"""

        _create_dict_type(api_client, code="list_a", name="A")
        _create_dict_type(api_client, code="list_b", name="B")
        response = api_client.get("/api/v1/dict-types")
        assert response.status_code == 200
        codes = [d["code"] for d in response.json()]
        assert "list_a" in codes
        assert "list_b" in codes

    def test_get_dict_type(self, api_client: TestClient) -> None:
        """查询字典类型详情。"""

        dt = _create_dict_type(api_client, code="get_dt", name="查询")
        response = api_client.get(f"/api/v1/dict-types/{dt['id']}")
        assert response.status_code == 200
        assert response.json()["code"] == "get_dt"

    def test_get_dict_type_404(self, api_client: TestClient) -> None:
        """查询不存在的字典类型返回 404。"""

        response = api_client.get(
            f"/api/v1/dict-types/{uuid4()}",
        )
        assert response.status_code == 404

    def test_update_dict_type(self, api_client: TestClient) -> None:
        """更新字典类型。"""

        dt = _create_dict_type(api_client, code="upd_dt", name="旧名")
        response = api_client.put(
            f"/api/v1/dict-types/{dt['id']}",
            json={"name": "新名", "description": "更新后"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "新名"
        assert body["description"] == "更新后"

    def test_enable_disable_dict_type(self, api_client: TestClient) -> None:
        """启用和禁用字典类型。"""

        dt = _create_dict_type(api_client, code="en_dt", name="开关")

        r = api_client.post(f"/api/v1/dict-types/{dt['id']}/disable")
        assert r.status_code == 200
        assert r.json()["status"] == "disabled"

        r = api_client.post(f"/api/v1/dict-types/{dt['id']}/enable")
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_delete_unreferenced_dict_type(self, api_client: TestClient) -> None:
        """删除未被引用的字典类型返回 204。"""

        dt = _create_dict_type(api_client, code="del_dt", name="删除")
        response = api_client.delete(f"/api/v1/dict-types/{dt['id']}")
        assert response.status_code == 204

    def test_delete_referenced_dict_type_409(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """被引用登记的字典类型删除返回 409 — SPEC 17.1."""

        dt = _create_dict_type(api_client, code="ref_protected", name="受保护")
        asyncio.run(_seed_reference(migrated_database_url, "ref_protected"))
        response = api_client.delete(f"/api/v1/dict-types/{dt['id']}")
        assert response.status_code == 409
        assert response.json()["type"] == "urn:apex:problem:dict.type_referenced"


# ═══════════════════════════════════════════════════════════════════════════════
# 字典项 API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestDictItemAPI:
    """字典项 API 契约测试 — SPEC 17.2."""

    def test_create_item_with_full_fields(self, api_client: TestClient) -> None:
        """创建字典项含显示文本/稳定值/排序/扩展元数据 — SPEC 17.2."""

        dt = _create_dict_type(api_client, code="item_full", name="完整")
        body = _create_dict_item(
            api_client,
            str(dt["id"]),
            label="紧急",
            value="urgent",
            sort_order=3,
            metadata={"color": "#ff0000", "icon": "alert"},
            description="紧急级别",
        )
        assert body["label"] == "紧急"
        assert body["value"] == "urgent"
        assert body["sort_order"] == 3
        assert body["metadata"] == {"color": "#ff0000", "icon": "alert"}
        assert body["description"] == "紧急级别"

    def test_create_item_duplicate_value_409(
        self,
        api_client: TestClient,
    ) -> None:
        """字典项稳定值冲突返回 409。"""

        dt = _create_dict_type(api_client, code="item_dup", name="重复")
        _create_dict_item(api_client, str(dt["id"]), value="same")
        response = api_client.post(
            f"/api/v1/dict-types/{dt['id']}/items",
            json={"label": "B", "value": "same", "sort_order": 0},
        )
        assert response.status_code == 409

    def test_list_items_ordered(self, api_client: TestClient) -> None:
        """字典项列表按 sort_order 升序排列。"""

        dt = _create_dict_type(api_client, code="item_list", name="列表")
        _create_dict_item(
            api_client,
            str(dt["id"]),
            label="C",
            value="c",
            sort_order=2,
        )
        _create_dict_item(
            api_client,
            str(dt["id"]),
            label="A",
            value="a",
            sort_order=0,
        )
        response = api_client.get(f"/api/v1/dict-types/{dt['id']}/items")
        assert response.status_code == 200
        items = response.json()
        assert [i["sort_order"] for i in items] == [0, 2]

    def test_get_item(self, api_client: TestClient) -> None:
        """查询字典项详情。"""

        dt = _create_dict_type(api_client, code="item_get", name="查询")
        item = _create_dict_item(api_client, str(dt["id"]), value="detail")
        response = api_client.get(
            f"/api/v1/dict-types/{dt['id']}/items/{item['id']}",
        )
        assert response.status_code == 200
        assert response.json()["value"] == "detail"

    def test_update_item(self, api_client: TestClient) -> None:
        """更新字典项。"""

        dt = _create_dict_type(api_client, code="item_upd", name="更新")
        item = _create_dict_item(api_client, str(dt["id"]), value="old")
        response = api_client.put(
            f"/api/v1/dict-types/{dt['id']}/items/{item['id']}",
            json={
                "label": "新",
                "value": "new",
                "sort_order": 5,
                "metadata": {"k": "v"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["label"] == "新"
        assert body["value"] == "new"
        assert body["sort_order"] == 5

    def test_enable_disable_item(self, api_client: TestClient) -> None:
        """启用和禁用字典项。"""

        dt = _create_dict_type(api_client, code="item_en", name="开关")
        item = _create_dict_item(api_client, str(dt["id"]), value="toggle")

        r = api_client.post(
            f"/api/v1/dict-types/{dt['id']}/items/{item['id']}/disable",
        )
        assert r.status_code == 200
        assert r.json()["status"] == "disabled"

        r = api_client.post(
            f"/api/v1/dict-types/{dt['id']}/items/{item['id']}/enable",
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_delete_item(self, api_client: TestClient) -> None:
        """删除字典项返回 204。"""

        dt = _create_dict_type(api_client, code="item_del", name="删除")
        item = _create_dict_item(api_client, str(dt["id"]), value="gone")
        response = api_client.delete(
            f"/api/v1/dict-types/{dt['id']}/items/{item['id']}",
        )
        assert response.status_code == 204
