"""系统配置模块 API 契约测试 — SPEC 16.1 / 16.2 / 28.4.

覆盖验收标准:
  - 配置项 API 契约：创建/查询/更新/启用禁用/分组管理。
  - 配置键在分组内唯一。
  - 配置值按声明类型保存时校验。
  - 敏感配置加密存储且 API 响应不回显明文（掩码）。
  - 核心安全配置不可被普通后台配置覆盖。

使用 TestClient 对真实应用发请求，覆盖认证/权限依赖以聚焦路由契约。
连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
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
    """清理系统配置与审计表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM sysconfig_items"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


# ── 测试 fixture ───────────────────────────────────────────────────────────

_TEST_ACTOR_ID = "00000000-0000-0000-0000-0000000000ee"


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


_SUPER_ADMIN_CTX = UseCaseContext(
    request_id="test-sysconfig-api-req",
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


@pytest.fixture()
def api_client(migrated_database_url: str) -> Iterator[TestClient]:
    """创建带系统配置模块和超管权限的 TestClient。"""

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


def _create_config(
    client: TestClient,
    *,
    group: str = "app",
    key: str = "test_key",
    value_type: str = "string",
    value: str = "test_value",
    is_sensitive: bool = False,
    is_core_security: bool = False,
    description: str | None = None,
) -> dict[str, object]:
    """通过 API 创建配置项并返回响应体。"""

    payload: dict[str, object] = {
        "group": group,
        "key": key,
        "valueType": value_type,
        "value": value,
    }
    if is_sensitive:
        payload["isSensitive"] = True
    if is_core_security:
        payload["isCoreSecurity"] = True
    if description is not None:
        payload["description"] = description
    response = client.post("/api/v1/configs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 配置项 API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestConfigApiContract:
    """配置项 API 契约测试 — SPEC 16.1."""

    def test_create_config_returns_201_with_location(
        self,
        api_client: TestClient,
    ) -> None:
        """创建配置项返回 201 + Location — SPEC 9.3."""

        response = api_client.post(
            "/api/v1/configs",
            json={
                "group": "app",
                "key": "site_name",
                "valueType": "string",
                "value": "My App",
            },
        )
        assert response.status_code == 201
        assert "location" in {k.lower() for k in response.headers}
        body = response.json()
        assert body["group"] == "app"
        assert body["key"] == "site_name"
        assert body["value"] == "My App"
        assert body["status"] == "active"

    def test_create_duplicate_key_returns_409(
        self,
        api_client: TestClient,
    ) -> None:
        """配置键在分组内唯一——重复返回 409 — SPEC 16.1."""

        _create_config(api_client, group="dup", key="k", value="v1")
        response = api_client.post(
            "/api/v1/configs",
            json={
                "group": "dup",
                "key": "k",
                "valueType": "string",
                "value": "v2",
            },
        )
        assert response.status_code == 409

    def test_create_invalid_type_value_returns_400(
        self,
        api_client: TestClient,
    ) -> None:
        """配置值类型校验——非法值返回 400 — SPEC 16.1."""

        response = api_client.post(
            "/api/v1/configs",
            json={
                "group": "types",
                "key": "bad_int",
                "valueType": "int",
                "value": "not-a-number",
            },
        )
        assert response.status_code == 400

    def test_get_config_by_id(self, api_client: TestClient) -> None:
        """查询配置项详情."""

        created = _create_config(api_client, key="port")
        response = api_client.get(f"/api/v1/configs/{created['id']}")
        assert response.status_code == 200
        assert response.json()["key"] == "port"

    def test_get_config_not_found_returns_404(
        self,
        api_client: TestClient,
    ) -> None:
        """查询不存在的配置项返回 404."""

        response = api_client.get(f"/api/v1/configs/{uuid4()}")
        assert response.status_code == 404

    def test_list_configs(self, api_client: TestClient) -> None:
        """查询配置项列表."""

        _create_config(api_client, group="list_g", key="k1")
        _create_config(api_client, group="list_g", key="k2")
        response = api_client.get("/api/v1/configs")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2

    def test_list_configs_by_group(self, api_client: TestClient) -> None:
        """按分组查询配置项."""

        _create_config(api_client, group="filter_g", key="k1")
        _create_config(api_client, group="other_g", key="k2")
        response = api_client.get("/api/v1/configs?group=filter_g")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["group"] == "filter_g"

    def test_list_groups(self, api_client: TestClient) -> None:
        """查询配置分组列表."""

        _create_config(api_client, group="grp1", key="k1")
        _create_config(api_client, group="grp2", key="k2")
        response = api_client.get("/api/v1/configs/groups")
        assert response.status_code == 200
        groups = [g["group"] for g in response.json()]
        assert "grp1" in groups
        assert "grp2" in groups

    def test_update_config(self, api_client: TestClient) -> None:
        """更新配置项."""

        created = _create_config(api_client, key="timeout", value="30")
        response = api_client.put(
            f"/api/v1/configs/{created['id']}",
            json={"value": "60", "description": "updated"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["value"] == "60"
        assert body["description"] == "updated"

    def test_enable_disable_config(self, api_client: TestClient) -> None:
        """启用和禁用配置项."""

        created = _create_config(api_client, key="feature")
        # 禁用
        response = api_client.post(f"/api/v1/configs/{created['id']}/disable")
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"
        # 启用
        response = api_client.post(f"/api/v1/configs/{created['id']}/enable")
        assert response.status_code == 200
        assert response.json()["status"] == "active"

    def test_unknown_field_in_create_rejected(
        self,
        api_client: TestClient,
    ) -> None:
        """创建请求拒绝未知字段 — SPEC 9.2."""

        response = api_client.post(
            "/api/v1/configs",
            json={
                "group": "test",
                "key": "k",
                "valueType": "string",
                "value": "v",
                "unknown_field": "bad",
            },
        )
        assert response.status_code == 422


@pytest.mark.g3
@pytest.mark.api
class TestSensitiveConfigApi:
    """敏感配置加密与掩码 API 测试 — SPEC 16.1 / 23.2."""

    def test_sensitive_config_masked_in_response(
        self,
        api_client: TestClient,
    ) -> None:
        """敏感配置 API 响应不回显明文（掩码）— SPEC 16.1."""

        created = _create_config(
            api_client,
            group="secrets",
            key="api_key",
            value="my-secret-api-key",
            is_sensitive=True,
        )
        assert created["value"] == "***MASKED***"
        assert created["isSensitive"] is True

    def test_sensitive_config_update_masked(
        self,
        api_client: TestClient,
    ) -> None:
        """更新敏感配置后响应仍掩码."""

        created = _create_config(
            api_client,
            key="secret",
            value="old-value",
            is_sensitive=True,
        )
        response = api_client.put(
            f"/api/v1/configs/{created['id']}",
            json={"value": "new-value"},
        )
        assert response.status_code == 200
        assert response.json()["value"] == "***MASKED***"

    def test_sensitive_config_not_in_list(
        self,
        api_client: TestClient,
    ) -> None:
        """敏感配置在列表中也掩码."""

        _create_config(
            api_client,
            group="list_secrets",
            key="s1",
            value="plaintext",
            is_sensitive=True,
        )
        response = api_client.get("/api/v1/configs?group=list_secrets")
        assert response.status_code == 200
        assert response.json()[0]["value"] == "***MASKED***"


@pytest.mark.g3
@pytest.mark.api
class TestCoreSecurityApi:
    """核心安全配置保护 API 测试 — SPEC 16.1."""

    def test_core_security_update_returns_409(
        self,
        api_client: TestClient,
    ) -> None:
        """核心安全配置不可通过普通后台更新 — SPEC 16.1."""

        created = _create_config(
            api_client,
            group="security",
            key="policy",
            value="strict",
            is_core_security=True,
        )
        response = api_client.put(
            f"/api/v1/configs/{created['id']}",
            json={"value": "lax"},
        )
        assert response.status_code == 409

    def test_core_security_disable_returns_409(
        self,
        api_client: TestClient,
    ) -> None:
        """核心安全配置不可通过普通后台禁用."""

        created = _create_config(
            api_client,
            group="security",
            key="enforce",
            value="true",
            value_type="bool",
            is_core_security=True,
        )
        response = api_client.post(f"/api/v1/configs/{created['id']}/disable")
        assert response.status_code == 409
