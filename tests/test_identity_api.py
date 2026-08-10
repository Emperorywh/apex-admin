"""用户模块 API 契约测试 — SPEC 9.3 / 11.1 / 28.4.

覆盖验收标准:
  - AC-0: 用户 API 契约测试全部通过: 创建 201+Location、详情、分页列表、
          资料更新、启用、禁用、重置密码、自助查询/更新资料、自助改密；
          extra=forbid 与错误码符合规范。
  - AC-1: 自助端点仅允许白名单字段；自助改密必须校验旧密码。

SPEC 28.4: 测试 HTTP 状态码、分页排序、RFC 9457 错误结构。
使用 TestClient 对真实应用发请求，连接真实 PostgreSQL（SPEC 28.2）。
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
from app.core.context.dependencies import create_use_case_context
from app.infrastructure.db.engine import create_db_engine
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

# ── 测试 fixture ──────────────────────────────────────────────────────────

_VALID_PASSWORD = "secure_password_12"


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移，确保 users 和 audit_logs 表存在。"""

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
                await conn.execute(text("DELETE FROM audit_logs"))
                await conn.execute(text("DELETE FROM users"))

        asyncio.run(_cleanup())
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clean_tables(migrated_database_url: str) -> Iterator[None]:
    """每个测试前清理 users 和 audit_logs 表。"""

    engine = create_db_engine(migrated_database_url)
    asyncio.run(_async_clean(engine))
    yield
    asyncio.run(_async_clean(engine))
    asyncio.run(engine.dispose())


async def _async_clean(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM audit_logs"))
        await conn.execute(text("DELETE FROM users"))


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


def _create_user(
    client: TestClient,
    *,
    username: str = "alice",
    display_name: str = "Alice",
    password: str = _VALID_PASSWORD,
) -> dict[str, object]:
    """通过 API 创建用户并返回响应体。"""
    response = client.post(
        "/api/v1/users",
        json={
            "username": username,
            "display_name": display_name,
            "password": password,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 创建用户 — 201 + Location
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_create_user_returns_201_with_location(api_client: TestClient) -> None:
    """创建用户返回 201 + Location 头 — SPEC 9.3."""
    response = api_client.post(
        "/api/v1/users",
        json={
            "username": "alice",
            "display_name": "Alice",
            "password": _VALID_PASSWORD,
        },
    )
    assert response.status_code == 201
    assert "Location" in response.headers
    user_id = response.json()["id"]
    assert response.headers["Location"] == f"/api/v1/users/{user_id}"
    # 响应不含敏感字段
    body = response.json()
    assert "password_hash" not in body
    assert "password" not in body
    assert body["status"] == "active"


@pytest.mark.g2
@pytest.mark.api
def test_create_user_unknown_field_returns_422(api_client: TestClient) -> None:
    """创建用户携带未知字段返回 422 — SPEC 9.2: extra="forbid"."""
    response = api_client.post(
        "/api/v1/users",
        json={
            "username": "alice",
            "display_name": "Alice",
            "password": _VALID_PASSWORD,
            "extra_field": "value",
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.g2
@pytest.mark.api
def test_create_duplicate_username_returns_409(api_client: TestClient) -> None:
    """重复用户名返回 409 — SPEC 8.4: 稳定冲突错误码."""
    _create_user(api_client, username="alice")
    response = api_client.post(
        "/api/v1/users",
        json={
            "username": "alice",
            "display_name": "Alice 2",
            "password": _VALID_PASSWORD,
        },
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "USER.ALREADY_EXISTS"


# ═══════════════════════════════════════════════════════════════════════════════
# 详情查询
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_get_user_returns_200(api_client: TestClient) -> None:
    """查询用户详情返回资源 Schema — SPEC 9.3."""
    user = _create_user(api_client)
    response = api_client.get(f"/api/v1/users/{user['id']}")
    assert response.status_code == 200
    assert response.json()["username"] == "alice"


@pytest.mark.g2
@pytest.mark.api
def test_get_nonexistent_returns_404(api_client: TestClient) -> None:
    """查询不存在用户返回 404 problem+json — SPEC 10.1."""
    response = api_client.get(
        f"/api/v1/users/{uuid4()}",
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "USER.NOT_FOUND"
    assert body["type"] == "urn:apex:problem:user.not_found"


# ═══════════════════════════════════════════════════════════════════════════════
# 分页列表
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_list_users_pagination(api_client: TestClient) -> None:
    """分页查询用户 — SPEC 9.4 响应结构."""
    for i in range(3):
        _create_user(api_client, username=f"user-{i}")

    response = api_client.get(
        "/api/v1/users",
        params={"page": 1, "page_size": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 2


@pytest.mark.g2
@pytest.mark.api
def test_list_users_sorting(api_client: TestClient) -> None:
    """排序查询 — SPEC 9.4 排序白名单."""
    for name in ["charlie", "alpha", "bravo"]:
        _create_user(api_client, username=name, display_name=name.capitalize())

    response = api_client.get(
        "/api/v1/users",
        params={"sort": "username", "page_size": 10},
    )
    assert response.status_code == 200
    usernames = [u["username"] for u in response.json()["items"]]
    assert usernames == ["alpha", "bravo", "charlie"]


@pytest.mark.g2
@pytest.mark.api
def test_invalid_sort_field_returns_400(api_client: TestClient) -> None:
    """非白名单排序字段返回 400 — SPEC 9.4 / 23.3."""
    response = api_client.get(
        "/api/v1/users",
        params={"sort": "password_hash"},
    )
    assert response.status_code == 400


@pytest.mark.g2
@pytest.mark.api
def test_list_users_filter_by_status(api_client: TestClient) -> None:
    """按状态筛选 — SPEC 9.4."""
    user1 = _create_user(api_client, username="active-user")
    _create_user(api_client, username="disabled-user")

    # 禁用第二个用户
    api_client.post(f"/api/v1/users/{user1['id']}/disable")

    response = api_client.get(
        "/api/v1/users",
        params={"status": "disabled"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "disabled"


# ═══════════════════════════════════════════════════════════════════════════════
# 更新资料
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_update_user(api_client: TestClient) -> None:
    """更新用户资料 — SPEC 11.1."""
    user = _create_user(api_client)
    response = api_client.put(
        f"/api/v1/users/{user['id']}",
        json={"display_name": "Updated", "phone": "13800138000", "email": None},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Updated"


# ═══════════════════════════════════════════════════════════════════════════════
# 启用/禁用
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_disable_user(api_client: TestClient) -> None:
    """禁用用户 — SPEC 11.1."""
    user = _create_user(api_client)
    response = api_client.post(f"/api/v1/users/{user['id']}/disable")
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


@pytest.mark.g2
@pytest.mark.api
def test_enable_user(api_client: TestClient) -> None:
    """启用用户 — SPEC 11.1."""
    user = _create_user(api_client)
    api_client.post(f"/api/v1/users/{user['id']}/disable")
    response = api_client.post(f"/api/v1/users/{user['id']}/enable")
    assert response.status_code == 200
    assert response.json()["status"] == "active"


@pytest.mark.g2
@pytest.mark.api
def test_disable_already_disabled_returns_409(api_client: TestClient) -> None:
    """禁用已禁用用户返回 409 — SPEC 11.1."""
    user = _create_user(api_client)
    api_client.post(f"/api/v1/users/{user['id']}/disable")
    response = api_client.post(f"/api/v1/users/{user['id']}/disable")
    assert response.status_code == 409
    assert response.json()["code"] == "USER.ALREADY_DISABLED"


# ═══════════════════════════════════════════════════════════════════════════════
# 重置密码
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_reset_password_returns_204(api_client: TestClient) -> None:
    """管理员重置密码返回 204 — SPEC 9.3 / 11.1."""
    user = _create_user(api_client)
    response = api_client.post(
        f"/api/v1/users/{user['id']}/reset-password",
        json={"new_password": "new_secure_password_12"},
    )
    assert response.status_code == 204


# ═══════════════════════════════════════════════════════════════════════════════
# 物理删除
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_delete_user_with_audit_returns_409(api_client: TestClient) -> None:
    """已产生审计记录的用户物理删除返回 409 — SPEC 11.3."""
    user = _create_user(api_client)  # 创建即产生审计记录
    response = api_client.delete(f"/api/v1/users/{user['id']}")
    assert response.status_code == 409
    assert response.json()["code"] == "USER.HAS_AUDIT_RECORDS"


# ═══════════════════════════════════════════════════════════════════════════════
# 自助端点
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def api_client_with_actor(
    migrated_database_url: str,
) -> Iterator[tuple[TestClient, str]]:
    """创建带 actor_id 的 TestClient——自助端点测试。

    通过覆盖 ``create_use_case_context`` 依赖注入模拟认证，
    使自助端点的 ``ctx.actor_id`` 指向已创建的用户。
    """
    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=migrated_database_url,
    )
    app = create_app(settings)

    # 先创建用户并获取其 ID（使用原始 client）
    with TestClient(app) as setup_client:
        user = _create_user(setup_client)
        actor_id = str(user["id"])

    # 覆盖 context 依赖，模拟已认证
    def _override_context() -> UseCaseContext:
        return UseCaseContext(
            request_id="test-self-req",
            actor_id=actor_id,
        )

    app.dependency_overrides[create_use_case_context] = _override_context

    with TestClient(app) as client:
        yield client, actor_id


@pytest.mark.g2
@pytest.mark.api
def test_get_self_profile(
    api_client_with_actor: tuple[TestClient, str],
) -> None:
    """自助查询个人资料 — SPEC 11.1."""
    client, _ = api_client_with_actor
    response = client.get("/api/v1/users/me")
    assert response.status_code == 200
    assert response.json()["username"] == "alice"


@pytest.mark.g2
@pytest.mark.api
def test_update_self_profile(
    api_client_with_actor: tuple[TestClient, str],
) -> None:
    """自助更新个人资料 — SPEC 11.1 / AC-1 白名单字段."""
    client, _ = api_client_with_actor
    response = client.put(
        "/api/v1/users/me",
        json={"display_name": "Self Updated", "phone": "13900000000", "email": None},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Self Updated"


@pytest.mark.g2
@pytest.mark.api
def test_self_profile_rejects_username(
    api_client_with_actor: tuple[TestClient, str],
) -> None:
    """自助更新不允许 username — SPEC 11.1 / AC-1 白名单."""
    client, _ = api_client_with_actor
    response = client.put(
        "/api/v1/users/me",
        json={
            "display_name": "Alice",
            "username": "hacker",
        },
    )
    assert response.status_code == 422


@pytest.mark.g2
@pytest.mark.api
def test_self_profile_rejects_status(
    api_client_with_actor: tuple[TestClient, str],
) -> None:
    """自助更新不允许 status — SPEC 11.1 / AC-1 白名单."""
    client, _ = api_client_with_actor
    response = client.put(
        "/api/v1/users/me",
        json={
            "display_name": "Alice",
            "status": "disabled",
        },
    )
    assert response.status_code == 422


@pytest.mark.g2
@pytest.mark.api
def test_self_change_password_correct_old(
    api_client_with_actor: tuple[TestClient, str],
) -> None:
    """自助改密——正确旧密码成功 — SPEC 11.1."""
    client, _ = api_client_with_actor
    response = client.put(
        "/api/v1/users/me/password",
        json={
            "old_password": _VALID_PASSWORD,
            "new_password": "new_secure_password_12",
        },
    )
    assert response.status_code == 204


@pytest.mark.g2
@pytest.mark.api
def test_self_change_password_wrong_old(
    api_client_with_actor: tuple[TestClient, str],
) -> None:
    """自助改密——错误旧密码返回 409 — SPEC 11.1 / AC-1."""
    client, _ = api_client_with_actor
    response = client.put(
        "/api/v1/users/me/password",
        json={
            "old_password": "wrong_password_12",
            "new_password": "new_secure_password_12",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "USER.INVALID_OLD_PASSWORD"


@pytest.mark.g2
@pytest.mark.api
def test_self_change_password_missing_old_returns_422(
    api_client_with_actor: tuple[TestClient, str],
) -> None:
    """自助改密缺少 old_password 返回 422 — SPEC 11.1 / AC-1."""
    client, _ = api_client_with_actor
    response = client.put(
        "/api/v1/users/me/password",
        json={
            "new_password": "new_secure_password_12",
        },
    )
    assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 未认证自助端点返回 401
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_self_profile_without_auth_returns_401(api_client: TestClient) -> None:
    """未认证访问自助端点返回 401 — SPEC 23.5."""
    response = api_client.get("/api/v1/users/me")
    assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# OpenAPI 验证
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_openapi_contains_identity_routes(api_client: TestClient) -> None:
    """OpenAPI 包含用户模块路由 — SPEC 9.6."""
    response = api_client.get("/openapi.json")
    schema = response.json()

    identity_paths = [
        p for p in schema.get("paths", {}) if p.startswith("/api/v1/users")
    ]
    assert len(identity_paths) >= 5


@pytest.mark.g2
@pytest.mark.api
def test_openapi_identity_operation_ids_unique(
    api_client: TestClient,
) -> None:
    """用户模块 operationId 全局唯一 — SPEC 28.4."""
    response = api_client.get("/openapi.json")
    schema = response.json()

    operation_ids: list[str] = []
    for path_data in schema.get("paths", {}).values():
        for method_data in path_data.values():
            if isinstance(method_data, dict) and "operationId" in method_data:
                operation_ids.append(method_data["operationId"])

    # 全局唯一
    assert len(operation_ids) == len(set(operation_ids))

    # 包含 identity 路由的 operationId
    identity_ops = [
        oid
        for oid in operation_ids
        if oid
        in (
            "create_user",
            "get_user",
            "list_users",
            "update_user",
            "enable_user",
            "disable_user",
            "reset_user_password",
            "delete_user",
            "get_self_profile",
            "update_self_profile",
            "change_self_password",
        )
    ]
    assert len(identity_ops) >= 8
