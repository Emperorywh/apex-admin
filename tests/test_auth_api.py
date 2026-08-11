"""认证模块 API 契约测试 — SPEC 12.1 / 12.2 / 12.3 / 12.4 / 28.4.

覆盖验收标准（SPEC 34.2）:
  - 登录端点返回 Access Token + Set-Cookie Refresh Token + Cache-Control: no-store。
  - 刷新端点轮换 Token 并校验 Origin。
  - 退出端点吊销会话并删除 Cookie。
  - 查看会话列表。
  - 强制下线端点（权限依赖覆盖）。

使用 TestClient 对真实应用发请求，连接真实 PostgreSQL（SPEC 28.2）。
不覆盖认证依赖——测试真实认证流程。
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

_VALID_PASSWORD = "secure_password_12"


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
    """清理全部认证、RBAC、用户和审计相关表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            for table in (
                "auth_refresh_tokens",
                "auth_login_attempts",
                "auth_sessions",
                "rbac_user_roles",
                "rbac_role_permissions",
                "rbac_permissions",
                "rbac_roles",
                "login_logs",
                "audit_logs",
                "users",
            ):
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


async def _create_test_user(
    database_url: str,
    *,
    username: str = "testuser",
    password: str = _VALID_PASSWORD,
    status: str = "active",
) -> str:
    """直接在数据库中创建测试用户，返回用户 ID 字符串。"""

    from app.core.security.password import Argon2Hasher

    hasher = Argon2Hasher()
    password_hash = hasher.hash(password)
    user_id = uuid4()
    now = datetime.now(UTC)

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, password_hash, "
                    "status, phone, email, last_login_at, password_updated_at, "
                    "created_at, updated_at, created_by, updated_by) "
                    "VALUES (:id, :u, :d, :p, :s, "
                    "NULL, NULL, NULL, :t, :t, :t, NULL, NULL)",
                ),
                {
                    "id": str(user_id),
                    "u": username,
                    "d": username.title(),
                    "p": password_hash,
                    "s": status,
                    "t": now,
                },
            )
    finally:
        await engine.dispose()
    return str(user_id)


# ── 测试 fixture ───────────────────────────────────────────────────────────


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


def _make_app(database_url: str) -> TestClient:
    """创建带真实数据库的 TestClient——不覆盖任何认证依赖。"""

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=database_url,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def auth_client(migrated_database_url: str) -> Iterator[TestClient]:
    """认证 API 测试客户端——完整认证流程。"""

    yield from _make_app(migrated_database_url)


def _super_admin_auth_override() -> ActorAuthorization:
    """模拟超管授权——用于 force-offline 端点测试。"""

    return ActorAuthorization(
        ctx=UseCaseContext(
            request_id="test-req",
            actor_id="00000000-0000-0000-0000-000000000001",
        ),
        permissions=frozenset(),
        is_super_admin=True,
    )


def _super_admin_ctx_override() -> UseCaseContext:
    """模拟认证上下文——用于 force-offline 端点测试。"""

    return UseCaseContext(
        request_id="test-req",
        actor_id="00000000-0000-0000-0000-000000000001",
        session_id="00000000-0000-0000-0000-000000000002",
    )


@pytest.fixture()
def admin_client(migrated_database_url: str) -> Iterator[TestClient]:
    """管理端测试客户端——覆盖认证和权限依赖。"""

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


# ═══════════════════════════════════════════════════════════════════════════════
# 登录端点
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_login_success_returns_access_token_and_cookie(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """登录成功返回 Access Token 和 Refresh Token Cookie — SPEC 12.1 / 12.2 / 12.4."""

    asyncio.run(_create_test_user(migrated_database_url))

    response = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": _VALID_PASSWORD},
    )
    assert response.status_code == 200
    # Cache-Control: no-store — SPEC 12.4
    assert response.headers.get("cache-control") == "no-store"

    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["expires_in"] == 900

    # Refresh Token 仅经 Set-Cookie，不进入 JSON — SPEC 12.2
    assert "refresh_token" not in body

    # Cookie 属性 — SPEC 12.4
    set_cookie = response.headers.get("set-cookie", "")
    assert "__Host-apex_refresh" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie
    assert "Domain=" not in set_cookie


@pytest.mark.g2
@pytest.mark.api
def test_login_wrong_password_returns_401(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """错误密码返回 401 — SPEC 12.4."""

    asyncio.run(_create_test_user(migrated_database_url))

    response = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": "wrong_password_12"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "AUTH.INVALID_CREDENTIALS"


@pytest.mark.g2
@pytest.mark.api
def test_login_disabled_user_returns_401(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """禁用用户登录返回 401 — SPEC 12.4."""

    asyncio.run(
        _create_test_user(migrated_database_url, status="disabled"),
    )

    response = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": _VALID_PASSWORD},
    )
    assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# 刷新端点
# ═══════════════════════════════════════════════════════════════════════════════


def _login_and_extract(
    client: TestClient,
    database_url: str,
) -> tuple[str, str]:
    """登录并返回 (access_token, refresh_cookie_value)。"""

    asyncio.run(_create_test_user(database_url))
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": _VALID_PASSWORD},
    )
    assert response.status_code == 200

    access_token = response.json()["access_token"]

    # 从 Set-Cookie 提取 refresh token 值
    cookies = response.cookies
    refresh_value = cookies.get("__Host-apex_refresh", "")
    assert refresh_value

    return access_token, refresh_value


@pytest.mark.g2
@pytest.mark.api
def test_refresh_success_returns_new_access_token(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """刷新成功返回新 Access Token — SPEC 12.2."""

    _, refresh_value = _login_and_extract(auth_client, migrated_database_url)

    response = auth_client.post(
        "/api/v1/auth/refresh",
        cookies={"__Host-apex_refresh": refresh_value},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert response.json()["access_token"]
    assert "refresh_token" not in response.json()


@pytest.mark.g2
@pytest.mark.api
def test_refresh_without_cookie_returns_401(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """无 Refresh Cookie 刷新返回 401 — SPEC 12.2."""

    response = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 401


@pytest.mark.g2
@pytest.mark.api
def test_refresh_invalid_origin_returns_403(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """非法 Origin 的刷新请求被拒绝 — SPEC 12.4."""

    _, refresh_value = _login_and_extract(auth_client, migrated_database_url)

    response = auth_client.post(
        "/api/v1/auth/refresh",
        cookies={"__Host-apex_refresh": refresh_value},
        headers={"Origin": "https://evil.com"},
    )
    assert response.status_code == 403


@pytest.mark.g2
@pytest.mark.api
def test_refresh_missing_origin_returns_403(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """缺少 Origin 头的刷新请求被拒绝 — SPEC 12.4."""

    _, refresh_value = _login_and_extract(auth_client, migrated_database_url)

    response = auth_client.post(
        "/api/v1/auth/refresh",
        cookies={"__Host-apex_refresh": refresh_value},
    )
    assert response.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 退出端点
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_logout_revokes_session_and_deletes_cookie(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """退出当前会话并删除 Cookie — SPEC 12.3 / 12.4."""

    access_token, _ = _login_and_extract(auth_client, migrated_database_url)

    response = auth_client.post(
        "/api/v1/auth/logout",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Origin": "http://localhost",
        },
    )
    assert response.status_code == 200

    # Cookie 应被删除
    set_cookie = response.headers.get("set-cookie", "")
    assert "__Host-apex_refresh" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()

    # 退出后 Access Token 失效 — SPEC 12.3
    # 使用 sessions 端点验证 Token 已失效
    sessions_resp = auth_client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert sessions_resp.status_code == 401


@pytest.mark.g2
@pytest.mark.api
def test_logout_invalid_origin_returns_403(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """退出时非法 Origin 返回 403 — SPEC 12.4."""

    access_token, _ = _login_and_extract(auth_client, migrated_database_url)

    response = auth_client.post(
        "/api/v1/auth/logout",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Origin": "https://evil.com",
        },
    )
    assert response.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 退出其他会话端点
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_logout_others_keeps_current(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """退出其他会话保留当前 — SPEC 12.3."""

    access_token, _ = _login_and_extract(auth_client, migrated_database_url)

    response = auth_client.post(
        "/api/v1/auth/logout-others",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    assert response.json()["revoked_count"] == 0  # 只有当前一个会话

    # 当前 Token 仍然有效
    sessions_resp = auth_client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert sessions_resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 会话列表端点
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_list_sessions_returns_own_sessions(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """查看活动会话列表 — SPEC 12.3."""

    access_token, _ = _login_and_extract(auth_client, migrated_database_url)

    response = auth_client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


@pytest.mark.g2
@pytest.mark.api
def test_list_sessions_without_token_returns_401(
    auth_client: TestClient,
) -> None:
    """无 Token 查看会话列表返回 401 — SPEC 23.5."""

    response = auth_client.get("/api/v1/auth/sessions")
    assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# 强制下线端点
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_force_offline_revokes_user_sessions(
    admin_client: TestClient,
    migrated_database_url: str,
) -> None:
    """管理员强制用户下线 — SPEC 12.3 / 18.1."""

    user_id = asyncio.run(_create_test_user(migrated_database_url))

    response = admin_client.post(f"/api/v1/auth/users/{user_id}/force-offline")
    assert response.status_code == 200
    assert response.json()["user_id"] == user_id
    assert response.json()["revoked_sessions"] == 0  # 无活跃会话


@pytest.mark.g2
@pytest.mark.api
def test_auth_use_case_cached_in_state(
    auth_client: TestClient,
    migrated_database_url: str,
) -> None:
    """AuthUseCase 构造后缓存到 app.state — SPEC 5.2 组合根装配."""

    asyncio.run(_create_test_user(migrated_database_url))

    # 第一次请求——构造并缓存
    auth_client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": _VALID_PASSWORD},
    )

    # 验证已缓存
    app = auth_client.app
    assert hasattr(app.state, "auth_use_case")
    assert app.state.auth_use_case is not None


@pytest.mark.g2
@pytest.mark.api
def test_unauthenticated_protected_endpoints_return_401(
    auth_client: TestClient,
) -> None:
    """受保护端点无 Token 返回 401 — SPEC 23.5."""

    # logout
    resp = auth_client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 401

    # logout-others
    resp = auth_client.post("/api/v1/auth/logout-others")
    assert resp.status_code == 401
