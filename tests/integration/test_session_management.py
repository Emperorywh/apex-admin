"""会话管理集成测试（SPEC §12.3、§28.3）。

使用 Testcontainers PostgreSQL 18 验证：
- 用户查看自己的活动会话
- 用户退出自己的会话
- 用户退出其他会话
- 管理员强制下线
- 最近活动时间条件更新（最多每 5 分钟）
- 每会话同时最多一个有效 Access Token

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
async def session_engine(
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
async def client(session_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    async with session_engine.begin() as conn:
        await conn.execute(text("DELETE FROM refresh_tokens"))
        await conn.execute(text("DELETE FROM access_tokens"))
        await conn.execute(text("DELETE FROM sessions"))
        await conn.execute(text("DELETE FROM users"))

    provider = _TestAuthProvider(session_engine)
    app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_and_login(
    client: AsyncClient,
    *,
    username: str = "sessuser",
) -> dict[str, str]:
    """创建用户并登录。"""
    resp = await client.post(
        "/api/v1/users",
        json={
            "username": username,
            "display_name": "Session User",
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

    users_resp = await client.get("/api/v1/users")
    user_id = ""
    for u in users_resp.json()["items"]:
        if u["username"] == username:
            user_id = u["id"]
            break

    return {
        "access_token": body["access_token"],
        "session_id": body["session_id"],
        "user_id": user_id,
    }


# ===========================================================================
# 会话列表测试
# ===========================================================================


class TestListSessions:
    """GET /api/v1/auth/sessions 测试（SPEC §12.3）。"""

    async def test_list_own_sessions(self, client: AsyncClient) -> None:
        """用户查看自己的活动会话。"""
        creds = await _create_and_login(client)

        resp = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
        )
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) >= 1
        assert sessions[0]["status"] == "active"

    async def test_list_multiple_sessions(self, client: AsyncClient) -> None:
        """多次登录产生多个会话，列表包含全部。"""
        creds = await _create_and_login(client, username="multisess")

        # 第二次登录
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "multisess", "password": _VALID_PASSWORD},
        )
        assert resp.status_code == 200

        resp = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
        )
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 2


# ===========================================================================
# 退出会话测试
# ===========================================================================


class TestRevokeSession:
    """DELETE /api/v1/auth/sessions/{session_id} 测试（SPEC §12.3）。"""

    async def test_revoke_own_session(self, client: AsyncClient) -> None:
        """用户退出自己的指定会话。"""
        creds = await _create_and_login(client)

        resp = await client.delete(
            f"/api/v1/auth/sessions/{creds['session_id']}",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
        )
        assert resp.status_code == 204

    async def test_revoke_other_own_session(
        self,
        client: AsyncClient,
    ) -> None:
        """用户退出自己的另一个会话（退出其他会话）。"""
        creds_a = await _create_and_login(client, username="othsess")

        # 第二次登录
        resp_b = await client.post(
            "/api/v1/auth/login",
            json={"username": "othsess", "password": _VALID_PASSWORD},
        )
        assert resp_b.status_code == 200
        session_b_id = resp_b.json()["session_id"]

        # 用会话 A 的 Token 退出会话 B
        resp = await client.delete(
            f"/api/v1/auth/sessions/{session_b_id}",
            headers={"Authorization": f"Bearer {creds_a['access_token']}"},
        )
        assert resp.status_code == 204

        # 验证会话 B 被吊销
        resp_list = await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {creds_a['access_token']}"},
        )
        sessions = resp_list.json()
        active_ids = [s["id"] for s in sessions]
        assert session_b_id not in active_ids


# ===========================================================================
# 管理员强制下线测试
# ===========================================================================


class TestForceLogout:
    """DELETE /api/v1/auth/users/{user_id}/sessions 测试（SPEC §12.3）。"""

    async def test_admin_force_logout(
        self,
        client: AsyncClient,
        session_engine: AsyncEngine,
    ) -> None:
        """管理员强制下线——吊销目标用户全部会话。"""
        # 创建两个用户
        creds_admin = await _create_and_login(client, username="adminfl")
        creds_target = await _create_and_login(client, username="targetfl")

        # 管理员强制下线目标用户
        resp = await client.delete(
            f"/api/v1/auth/users/{creds_target['user_id']}/sessions",
            headers={"Authorization": f"Bearer {creds_admin['access_token']}"},
        )
        assert resp.status_code == 204

        # 验证目标用户的会话全部被吊销
        async with session_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT status FROM sessions WHERE user_id != %s"  # noqa: S608
                ),
                (creds_admin["user_id"],),
            )
            statuses = [row[0] for row in result.fetchall()]
        assert all(s == "revoked" for s in statuses)


# ===========================================================================
# 每会话最多一个有效 Access Token
# ===========================================================================


class TestSingleAccessTokenPerSession:
    """每会话同时最多一个有效 Access Token（SPEC §12.2）。"""

    async def test_refresh_replaces_access_token(
        self,
        client: AsyncClient,
        session_engine: AsyncEngine,
    ) -> None:
        """刷新后旧 Access Token 被删除，每会话只有一个。"""
        await _create_and_login(client, username="singleat")

        refresh_cookie = client.cookies.get("__Host-apex_refresh")
        assert refresh_cookie is not None

        resp = await client.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": f"__Host-apex_refresh={refresh_cookie}"},
        )
        assert resp.status_code == 200

        async with session_engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM access_tokens"))
            count = result.fetchone()[0]
        assert count == 1


# ===========================================================================
# 最近活动时间条件更新
# ===========================================================================


class TestActivityTimeConditionalUpdate:
    """最近活动时间最多每 5 分钟条件更新（SPEC §12.3）。"""

    async def test_activity_not_updated_within_5_minutes(
        self,
        client: AsyncClient,
        session_engine: AsyncEngine,
    ) -> None:
        """短时间内多次请求不更新 last_activity_at。"""
        creds = await _create_and_login(client, username="acttest")

        # 第一次请求获取初始 last_activity_at
        async with session_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT last_activity_at FROM sessions LIMIT 1"),
            )
            initial_time = result.fetchone()[0]

        # 立即做第二次请求
        await client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {creds['access_token']}"},
        )

        async with session_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT last_activity_at FROM sessions LIMIT 1"),
            )
            second_time = result.fetchone()[0]

        # 5 分钟内不应更新
        assert initial_time == second_time
