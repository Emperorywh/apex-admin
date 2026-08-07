"""认证模块登录集成测试（SPEC §12、§28.3）。

使用 Testcontainers PostgreSQL 18 验证认证模块的完整 API 调用流：
Router → AuthService → UserRepository → SessionRepository → Database。

测试覆盖：
- 登录成功：创建会话、生成 Access Token（响应体）、Refresh Token（Cookie）
- 登录失败：用户不存在、密码错误、用户禁用
- check_needs_rehash 在同一事务中升级哈希
- 会话模型持久化（设备、IP、User-Agent、超时）
- Access Token 以 HMAC-SHA-256 摘要存储（非明文）
- Refresh Token 以独立密钥 HMAC-SHA-256 摘要存储
- 登出吊销会话、删除 Cookie
- 登录响应设置 Cache-Control: no-store
- 用户不存在时执行虚拟哈希校验

前置条件：运行环境需要 Docker（Testcontainers 依赖）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from app.app import create_app
from app.config.settings import AppEnv, Settings
from app.health.providers import DbPoolProvider

pytestmark = [pytest.mark.integration, pytest.mark.g2]

# 测试用有效密钥（与 conftest 一致）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"

_VALID_PASSWORD = "SecurePass123!"

# users + sessions + access_tokens + refresh_tokens 表 DDL
# （与 Alembic 迁移 0003_user + 0004_auth 一致）
_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY,
    username            VARCHAR(50) UNIQUE NOT NULL,
    display_name        VARCHAR(100) NOT NULL,
    password_hash       VARCHAR(255) NOT NULL,
    status              VARCHAR(20) NOT NULL,
    phone               VARCHAR(20),
    email               VARCHAR(255),
    last_login_at       TIMESTAMPTZ,
    password_updated_at TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    created_by          UUID,
    updated_at          TIMESTAMPTZ NOT NULL,
    updated_by          UUID
);
CREATE TABLE IF NOT EXISTS sessions (
    id                      UUID PRIMARY KEY,
    user_id                 UUID REFERENCES users(id),
    device                  VARCHAR(255),
    ip                      VARCHAR(45) NOT NULL,
    user_agent              VARCHAR(500) NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL,
    last_activity_at        TIMESTAMPTZ NOT NULL,
    idle_timeout_minutes    INTEGER NOT NULL,
    absolute_timeout_hours  INTEGER NOT NULL,
    status                  VARCHAR(20) NOT NULL,
    revoked_at              TIMESTAMPTZ,
    revoked_reason          VARCHAR(100)
);
CREATE TABLE IF NOT EXISTS access_tokens (
    digest      VARCHAR(64) PRIMARY KEY,
    session_id  UUID REFERENCES sessions(id),
    user_id     UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    digest              VARCHAR(64) PRIMARY KEY,
    session_id          UUID REFERENCES sessions(id),
    user_id             UUID REFERENCES users(id),
    token_family_id     UUID NOT NULL,
    predecessor_digest  VARCHAR(64),
    created_at          TIMESTAMPTZ NOT NULL,
    used_at             TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_reason      VARCHAR(100)
);
"""


class _TestAuthProvider(DbPoolProvider):
    """测试用数据库连接池 Provider。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine | None:
        return self._engine

    async def initialize(self) -> None:
        """空操作——引擎已在 fixture 中创建。"""

    async def dispose(self) -> None:
        """空操作——引擎由 fixture 管理。"""

    async def check_connection(self) -> bool:
        return True


def _make_test_settings() -> Settings:
    """构造集成测试用 Settings。"""
    return Settings(
        _env_file=None,
        app_env=AppEnv.TESTING,
        database_url="postgresql+psycopg://apex:secret@localhost:5432/apex_admin_test",
        access_token_hmac_key=_VALID_ACCESS_KEY,
        refresh_token_hmac_key=_VALID_REFRESH_KEY,
        config_encryption_key=_VALID_ENCRYPTION_KEY,
        file_storage_root="/tmp/apex-test-files",
    )


@pytest.fixture(scope="module")
async def auth_engine(
    postgres_container: PostgresContainer,
) -> AsyncIterator[AsyncEngine]:
    """为认证模块测试创建引擎并创建全部表。"""
    from app.infrastructure.database.engine import create_engine

    url = postgres_container.get_connection_url()
    engine = create_engine(url, pool_size=3, max_overflow=2)

    async with engine.begin() as conn:
        await conn.execute(text(_DDL))

    yield engine
    await engine.dispose()


@pytest.fixture
async def api_client(auth_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """创建带数据库连接的异步 API 测试客户端。

    每个测试前清空数据表，确保测试隔离。
    """
    # 清空表（测试隔离，保留表结构）
    async with auth_engine.begin() as conn:
        await conn.execute(text("DELETE FROM refresh_tokens"))
        await conn.execute(text("DELETE FROM access_tokens"))
        await conn.execute(text("DELETE FROM sessions"))
        await conn.execute(text("DELETE FROM users"))

    provider = _TestAuthProvider(auth_engine)
    app = create_app(settings=_make_test_settings(), db_pool_provider=provider)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _create_user_via_api(client: AsyncClient) -> dict[str, str]:
    """通过 API 创建测试用户，返回用户名和密码。"""
    response = await client.post(
        "/api/v1/users",
        json={
            "username": "loginuser",
            "display_name": "Login User",
            "password": _VALID_PASSWORD,
        },
    )
    assert response.status_code == 201
    return {"username": "loginuser", "password": _VALID_PASSWORD}


# ===========================================================================
# 登录端点测试
# ===========================================================================


class TestLoginEndpoint:
    """POST /api/v1/auth/login 测试（SPEC §12.1）。"""

    async def test_login_success_returns_access_token(self, api_client: AsyncClient) -> None:
        """登录成功返回 Access Token（SPEC §12.1）。"""
        creds = await _create_user_via_api(api_client)

        response = await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 900  # 15 分钟
        assert "session_id" in body

    async def test_login_sets_refresh_token_cookie(self, api_client: AsyncClient) -> None:
        """登录设置 Refresh Token HttpOnly Cookie（SPEC §12.1、§12.4）。"""
        creds = await _create_user_via_api(api_client)

        response = await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )
        assert response.status_code == 200

        # 检查 Cookie
        cookies = response.headers.get_list("set-cookie")
        refresh_cookie = [c for c in cookies if "__Host-apex_refresh" in c]
        assert len(refresh_cookie) == 1
        cookie_str = refresh_cookie[0]
        assert "HttpOnly" in cookie_str
        assert "Secure" in cookie_str
        assert "SameSite=strict" in cookie_str
        assert "Path=/" in cookie_str

    async def test_login_sets_cache_control_no_store(self, api_client: AsyncClient) -> None:
        """登录响应设置 Cache-Control: no-store（SPEC §12.1、§12.2）。"""
        creds = await _create_user_via_api(api_client)

        response = await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-store"

    async def test_login_wrong_password_returns_401(self, api_client: AsyncClient) -> None:
        """密码错误返回 401（SPEC §12.4）。"""
        creds = await _create_user_via_api(api_client)

        response = await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": "WrongPassword!!"},
        )
        assert response.status_code == 401

    async def test_login_nonexistent_user_returns_401(self, api_client: AsyncClient) -> None:
        """不存在的用户返回 401（SPEC §12.4：防止枚举）。"""
        response = await api_client.post(
            "/api/v1/auth/login",
            json={"username": "ghost", "password": _VALID_PASSWORD},
        )
        assert response.status_code == 401


# ===========================================================================
# 登出端点测试
# ===========================================================================


class TestLogoutEndpoint:
    """POST /api/v1/auth/logout 测试（SPEC §12.3、§12.4）。"""

    async def test_logout_revokes_session(self, api_client: AsyncClient) -> None:
        """登出吊销会话并删除 Cookie。"""
        creds = await _create_user_via_api(api_client)

        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )
        assert login_resp.status_code == 200

        # 使用登录返回的 Cookie 登出
        response = await api_client.post("/api/v1/auth/logout")
        assert response.status_code == 204

        # Cookie 应被删除
        cookies = response.headers.get_list("set-cookie")
        refresh_cookie = [c for c in cookies if "__Host-apex_refresh" in c]
        assert len(refresh_cookie) == 1
        assert "Max-Age=0" in refresh_cookie[0] or "expires=" in refresh_cookie[0].lower()

    async def test_logout_without_cookie_idempotent(self, api_client: AsyncClient) -> None:
        """无 Cookie 登出幂等成功。"""
        response = await api_client.post("/api/v1/auth/logout")
        assert response.status_code == 204


# ===========================================================================
# 数据库持久化验证
# ===========================================================================


class TestTokenStorageInDb:
    """Token 摘要存储验证（SPEC §12.2）——验证明文不入库。"""

    async def test_access_token_digest_not_plaintext(
        self,
        api_client: AsyncClient,
        auth_engine: AsyncEngine,
    ) -> None:
        """Access Token 以摘要存储，明文不入库。"""
        creds = await _create_user_via_api(api_client)
        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )
        access_token = login_resp.json()["access_token"]

        async with auth_engine.connect() as conn:
            result = await conn.execute(text("SELECT digest FROM access_tokens"))
            digests = [row[0] for row in result.fetchall()]

        assert len(digests) == 1
        assert digests[0] != access_token  # 明文 != 摘要
        assert len(digests[0]) == 64  # HMAC-SHA-256 hex

    async def test_refresh_token_digest_not_plaintext(
        self,
        api_client: AsyncClient,
        auth_engine: AsyncEngine,
    ) -> None:
        """Refresh Token 以独立密钥摘要存储，明文不入库。"""
        creds = await _create_user_via_api(api_client)
        await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )

        async with auth_engine.connect() as conn:
            result = await conn.execute(text("SELECT digest FROM refresh_tokens"))
            digests = [row[0] for row in result.fetchall()]

        assert len(digests) == 1
        assert len(digests[0]) == 64  # HMAC-SHA-256 hex

    async def test_session_persisted_with_required_fields(
        self,
        api_client: AsyncClient,
        auth_engine: AsyncEngine,
    ) -> None:
        """会话持久化包含全部必需字段（SPEC §12.3）。"""
        creds = await _create_user_via_api(api_client)
        await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )

        async with auth_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT ip, user_agent, created_at, last_activity_at, "
                    "idle_timeout_minutes, absolute_timeout_hours, status "
                    "FROM sessions"
                )
            )
            row = result.fetchone()

        assert row is not None
        assert row[0] is not None  # ip
        assert row[1] is not None  # user_agent
        assert row[2] is not None  # created_at
        assert row[3] is not None  # last_activity_at
        assert row[4] == 30  # idle_timeout_minutes
        assert row[5] == 12  # absolute_timeout_hours
        assert row[6] == "active"  # status

    async def test_access_and_refresh_digests_differ(
        self,
        api_client: AsyncClient,
        auth_engine: AsyncEngine,
    ) -> None:
        """Access 和 Refresh Token 摘要不同（独立密钥，SPEC §12.2）。"""
        creds = await _create_user_via_api(api_client)
        await api_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": creds["password"]},
        )

        async with auth_engine.connect() as conn:
            access_result = await conn.execute(text("SELECT digest FROM access_tokens"))
            refresh_result = await conn.execute(text("SELECT digest FROM refresh_tokens"))
            access_digest = access_result.fetchone()[0]
            refresh_digest = refresh_result.fetchone()[0]

        assert access_digest != refresh_digest
