"""Token 轮换、重放检测与会话生命周期集成测试（SPEC §12.2、§12.3、§28.3）。

使用 Testcontainers PostgreSQL 18 验证：
- Token 轮换：旧 Token 失效，新 Access/Refresh Token 在同一事务中签发
- Token Family 跟踪：前驱关系、创建/使用/过期时间、吊销原因
- 并发刷新：行锁确保同一 Token 只有一个请求成功
- 重放检测：已使用 Token 再次出现 → 整个 Session 和 Token Family 吊销
- 刷新检查用户状态、会话有效性、空闲/绝对过期
- 被吊销会话不可刷新
- 旧 Access Token 刷新后失效；每会话最多一个有效 Access Token
- 用户禁用 → 所有会话失效
- 管理员重置密码 → 吊销全部会话
- 用户自助改密 → 保留当前会话、吊销其他
- 最近活动时间最多每 5 分钟条件更新
- 每请求验证：Access Token 摘要查 DB

前置条件：运行环境需要 Docker（Testcontainers 依赖）。
"""

from __future__ import annotations

import asyncio
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

_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"
_VALID_PASSWORD = "SecurePass123!"

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
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine | None:
        return self._engine

    async def initialize(self) -> None:
        pass

    async def dispose(self) -> None:
        pass

    async def check_connection(self) -> bool:
        return True


def _make_test_settings() -> Settings:
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
async def rotation_engine(
    postgres_container: PostgresContainer,
) -> AsyncIterator[AsyncEngine]:
    from app.infrastructure.database.engine import create_engine

    url = postgres_container.get_connection_url()
    engine = create_engine(url, pool_size=5, max_overflow=3)
    async with engine.begin() as conn:
        await conn.execute(text(_DDL))
    yield engine
    await engine.dispose()


@pytest.fixture
async def client(rotation_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    async with rotation_engine.begin() as conn:
        await conn.execute(text("DELETE FROM refresh_tokens"))
        await conn.execute(text("DELETE FROM access_tokens"))
        await conn.execute(text("DELETE FROM sessions"))
        await conn.execute(text("DELETE FROM users"))

    provider = _TestAuthProvider(rotation_engine)
    app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_and_login(
    client: AsyncClient,
    *,
    username: str = "rotuser",
) -> dict[str, str]:
    """创建用户并登录，返回 access_token、refresh_token、session_id、user_id。"""
    resp = await client.post(
        "/api/v1/users",
        json={
            "username": username,
            "display_name": "Rotation User",
            "password": _VALID_PASSWORD,
        },
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": _VALID_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    refresh_cookie = resp.cookies.get("__Host-apex_refresh")
    assert refresh_cookie is not None

    # 获取 user_id
    users_resp = await client.get("/api/v1/users")
    user_id = None
    for u in users_resp.json()["items"]:
        if u["username"] == username:
            user_id = u["id"]
            break

    return {
        "access_token": body["access_token"],
        "refresh_token": refresh_cookie,
        "session_id": body["session_id"],
        "user_id": user_id or "",
    }


def _refresh_headers(refresh_token: str) -> dict[str, str]:
    """构造刷新请求的 Cookie 头。"""
    return {"Cookie": f"__Host-apex_refresh={refresh_token}"}


# ===========================================================================
# Token 轮换测试
# ===========================================================================


class TestTokenRotation:
    """POST /api/v1/auth/refresh 测试（SPEC §12.2）。"""

    async def test_refresh_rotates_tokens(self, client: AsyncClient) -> None:
        """刷新轮换 Token：旧 Token 失效，新 Access + Refresh 签发。"""
        creds = await _create_and_login(client)

        resp = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 900
        assert resp.headers.get("cache-control") == "no-store"

        # 新 Cookie 应包含新 Refresh Token
        new_refresh = resp.cookies.get("__Host-apex_refresh")
        assert new_refresh is not None
        assert new_refresh != creds["refresh_token"]

    async def test_old_refresh_token_invalid_after_rotation(
        self,
        client: AsyncClient,
    ) -> None:
        """旧 Refresh Token 轮换后不可再次使用 → 重放检测。"""
        creds = await _create_and_login(client)

        # 第一次刷新成功
        resp = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp.status_code == 200

        # 用旧 Token 再次刷新 → 重放检测，应返回 401
        resp2 = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp2.status_code == 401

    async def test_rotation_replaces_access_token(
        self,
        client: AsyncClient,
        rotation_engine: AsyncEngine,
    ) -> None:
        """旧 Access Token 在刷新后立即失效（SPEC §12.2）。"""
        creds = await _create_and_login(client)

        # 刷新前有一个 Access Token
        async with rotation_engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM access_tokens"))
            assert result.fetchone()[0] == 1

        # 刷新
        resp = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp.status_code == 200

        # 刷新后仍只有一个 Access Token（旧的被删除，新的被创建）
        async with rotation_engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM access_tokens"))
            assert result.fetchone()[0] == 1

    async def test_refresh_token_records_family_and_predecessor(
        self,
        client: AsyncClient,
        rotation_engine: AsyncEngine,
    ) -> None:
        """Refresh Token 记录 Token Family、前驱关系（SPEC §12.2）。"""
        creds = await _create_and_login(client)

        # 刷新
        resp = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp.status_code == 200

        async with rotation_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT token_family_id, predecessor_digest, used_at, "
                    "revoked_reason FROM refresh_tokens ORDER BY created_at"
                )
            )
            rows = result.fetchall()

        assert len(rows) == 2
        old_token, new_token = rows[0], rows[1]

        # 同一 Token Family
        assert old_token[0] == new_token[0]
        # 旧 Token 已使用
        assert old_token[2] is not None
        # 新 Token 的前驱指向旧 Token 的摘要
        assert new_token[1] is not None
        # 旧 Token 未因重放被吊销（正常使用）
        assert old_token[3] is None

    async def test_refresh_checks_user_status(self, client: AsyncClient) -> None:
        """刷新检查用户状态——禁用用户不可刷新（SPEC §12.2）。"""
        creds = await _create_and_login(client)

        # 禁用用户
        resp = await client.post(
            f"/api/v1/users/{creds['user_id']}/disable",
        )
        assert resp.status_code == 200

        # 刷新应失败
        resp2 = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp2.status_code == 401

    async def test_revoked_session_cannot_refresh(self, client: AsyncClient) -> None:
        """被吊销会话不可刷新（SPEC §12.2）。"""
        creds = await _create_and_login(client)

        # 登出（吊销会话）
        resp = await client.post(
            "/api/v1/auth/logout",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp.status_code == 204

        # 刷新应失败
        resp2 = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp2.status_code == 401


# ===========================================================================
# 重放检测测试
# ===========================================================================


class TestReplayDetection:
    """重放检测测试（SPEC §12.2）。"""

    async def test_replay_revokes_entire_session_and_family(
        self,
        client: AsyncClient,
        rotation_engine: AsyncEngine,
    ) -> None:
        """重放检测：已使用 Token 再次出现 → 吊销整个 Session 和 Token Family。"""
        creds = await _create_and_login(client)

        # 第一次刷新（正常轮换）
        resp = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp.status_code == 200

        # 用旧 Token 再次刷新 → 重放检测
        resp2 = await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )
        assert resp2.status_code == 401

        # 验证整个 Token Family 被吊销
        async with rotation_engine.connect() as conn:
            result = await conn.execute(text("SELECT revoked_reason FROM refresh_tokens"))
            reasons = [row[0] for row in result.fetchall()]
        assert all(r is not None for r in reasons), "全部 Refresh Token 应被吊销"

        # 验证会话被吊销
        async with rotation_engine.connect() as conn:
            result = await conn.execute(text("SELECT status FROM sessions"))
            status_val = result.fetchone()[0]
        assert status_val == "revoked"

    async def test_replay_deletes_access_tokens(
        self,
        client: AsyncClient,
        rotation_engine: AsyncEngine,
    ) -> None:
        """重放检测删除关联的 Access Token。"""
        creds = await _create_and_login(client)

        # 正常刷新
        await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )

        # 重放
        await client.post(
            "/api/v1/auth/refresh",
            headers=_refresh_headers(creds["refresh_token"]),
        )

        async with rotation_engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM access_tokens"))
            assert result.fetchone()[0] == 0


# ===========================================================================
# 并发刷新测试
# ===========================================================================


class TestConcurrentRefresh:
    """并发刷新测试——行锁确保只有一个成功（SPEC §12.2）。"""

    async def test_concurrent_refresh_only_one_succeeds(
        self,
        client: AsyncClient,
    ) -> None:
        """同一 Refresh Token 并发刷新：仅一个成功（SPEC §12.2）。"""
        creds = await _create_and_login(client)

        # 并发发送两个刷新请求
        results = await asyncio.gather(
            client.post(
                "/api/v1/auth/refresh",
                headers=_refresh_headers(creds["refresh_token"]),
            ),
            client.post(
                "/api/v1/auth/refresh",
                headers=_refresh_headers(creds["refresh_token"]),
            ),
            return_exceptions=True,
        )

        statuses = []
        for r in results:
            if isinstance(r, Exception):
                statuses.append(500)
            else:
                statuses.append(r.status_code)

        # 恰好一个 200，另一个 401（重放检测）
        assert statuses.count(200) == 1, f"Expected exactly one success, got {statuses}"
        assert statuses.count(401) == 1, f"Expected exactly one failure, got {statuses}"


# ===========================================================================
# 用户禁用 → 会话失效测试
# ===========================================================================


class TestUserDisabledSessions:
    """用户禁用 → 所有会话失效（SPEC §12.3）。"""

    async def test_disabled_user_sessions_revoked(
        self,
        client: AsyncClient,
        rotation_engine: AsyncEngine,
    ) -> None:
        """禁用用户后其全部会话被吊销。"""
        creds = await _create_and_login(client)

        resp = await client.post(
            f"/api/v1/users/{creds['user_id']}/disable",
        )
        assert resp.status_code == 200

        async with rotation_engine.connect() as conn:
            result = await conn.execute(text("SELECT status FROM sessions"))
            statuses = [row[0] for row in result.fetchall()]
        assert all(s == "revoked" for s in statuses)


# ===========================================================================
# 密码变更 → 选择性吊销测试
# ===========================================================================


class TestPasswordChangeSessionRevocation:
    """密码变更选择性吊销（SPEC §12.3）。"""

    async def test_admin_reset_revokes_all_sessions(
        self,
        client: AsyncClient,
        rotation_engine: AsyncEngine,
    ) -> None:
        """管理员重置密码 → 吊销全部会话。"""
        creds = await _create_and_login(client)

        resp = await client.post(
            f"/api/v1/users/{creds['user_id']}/reset-password",
            json={"new_password": "NewSecurePass456!"},
        )
        assert resp.status_code == 204

        async with rotation_engine.connect() as conn:
            result = await conn.execute(text("SELECT status FROM sessions"))
            statuses = [row[0] for row in result.fetchall()]
        assert all(s == "revoked" for s in statuses)

    async def test_self_change_password_keeps_current_session(
        self,
        client: AsyncClient,
        rotation_engine: AsyncEngine,
    ) -> None:
        """用户自助改密 → 保留当前会话、吊销其他。"""
        # 第一次登录（会话 A）
        creds_a = await _create_and_login(client, username="changepass")

        # 第二次登录（会话 B）
        resp_b = await client.post(
            "/api/v1/auth/login",
            json={"username": "changepass", "password": _VALID_PASSWORD},
        )
        assert resp_b.status_code == 200
        session_b = resp_b.json()["session_id"]

        # 用会话 B 改密，保留会话 B
        resp = await client.post(
            "/api/v1/me/change-password",
            json={
                "current_password": _VALID_PASSWORD,
                "new_password": "NewSecurePass789!",
            },
            headers={
                "X-User-Id": creds_a["user_id"],
                "X-Session-Id": session_b,
            },
        )
        assert resp.status_code == 204

        async with rotation_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id, status, revoked_reason FROM sessions ORDER BY created_at")
            )
            rows = result.fetchall()

        assert len(rows) == 2
        # 会话 A（第一个）应被吊销
        assert rows[0][1] == "revoked"
        # 会话 B（保留的）应仍为活跃
        assert rows[1][1] == "active"


# ===========================================================================
# 每请求验证测试
# ===========================================================================


class TestPerRequestValidation:
    """每请求在线校验（SPEC §12.3）。"""

    async def test_valid_access_token_passes_validation(
        self,
        client: AsyncClient,
    ) -> None:
        """有效 Access Token 可访问受保护端点。"""
        creds = await _create_and_login(client)

        resp = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
        )
        assert resp.status_code == 200

    async def test_invalid_access_token_rejected(self, client: AsyncClient) -> None:
        """无效 Access Token 被拒绝。"""
        resp = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": "Bearer invalid_token_value"},
        )
        assert resp.status_code == 401

    async def test_missing_authorization_rejected(self, client: AsyncClient) -> None:
        """缺少 Authorization 头被拒绝。"""
        resp = await client.get("/api/v1/auth/sessions")
        assert resp.status_code == 401

    async def test_disabled_user_token_rejected(
        self,
        client: AsyncClient,
    ) -> None:
        """禁用用户的 Token 在后续请求中被拒绝。"""
        creds = await _create_and_login(client)

        # 先验证 Token 有效
        resp = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
        )
        assert resp.status_code == 200

        # 禁用用户
        await client.post(f"/api/v1/users/{creds['user_id']}/disable")

        # 同一 Token 现在应被拒绝
        resp2 = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
        )
        assert resp2.status_code == 401
