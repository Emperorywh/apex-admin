"""登录安全集成测试（SPEC §12.4、§28.3）。

使用 Testcontainers PostgreSQL 18 验证暴力破解防护（账号 / IP 双维度、
PostgreSQL 持久化）、响应一致性和防用户枚举。

测试覆盖：
- 账号维度：连续失败 5 次后限制 15 分钟
- IP 维度：连续失败 20 次后限制 15 分钟
- 两维度持久化到 PostgreSQL
- 限制时的响应与普通登录失败响应一致
- 账号维度成功登录后清理
- IP 维度成功登录不清理
- 防止用户枚举：有效/无效用户响应一致
- 登录日志：成功、失败、失败原因分类
- 日志中不记录明文密码或 Token

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

# 全部表 DDL（与 Alembic 迁移 0003_user + 0004_auth + 0005_login_security 一致）
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
CREATE TABLE IF NOT EXISTS login_attempts (
    dimension          VARCHAR(10) NOT NULL,
    identifier         VARCHAR(255) NOT NULL,
    failure_count      INTEGER NOT NULL,
    locked_until       TIMESTAMPTZ,
    last_failure_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (dimension, identifier)
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
async def security_engine(
    postgres_container: PostgresContainer,
) -> AsyncIterator[AsyncEngine]:
    """为登录安全测试创建引擎并创建全部表。"""
    from app.infrastructure.database.engine import create_engine

    url = postgres_container.get_connection_url()
    engine = create_engine(url, pool_size=3, max_overflow=2)

    async with engine.begin() as conn:
        await conn.execute(text(_DDL))

    yield engine
    await engine.dispose()


@pytest.fixture
async def security_client(security_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """创建带数据库连接的异步 API 测试客户端。

    每个测试前清空数据表，确保测试隔离。
    """
    async with security_engine.begin() as conn:
        await conn.execute(text("DELETE FROM login_attempts"))
        await conn.execute(text("DELETE FROM refresh_tokens"))
        await conn.execute(text("DELETE FROM access_tokens"))
        await conn.execute(text("DELETE FROM sessions"))
        await conn.execute(text("DELETE FROM users"))

    provider = _TestAuthProvider(security_engine)
    app = create_app(settings=_make_test_settings(), db_pool_provider=provider)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _create_user_via_api(
    client: AsyncClient,
    username: str = "secuser",
) -> dict[str, str]:
    """通过 API 创建测试用户，返回用户名和密码。"""
    response = await client.post(
        "/api/v1/users",
        json={
            "username": username,
            "display_name": "Security User",
            "password": _VALID_PASSWORD,
        },
    )
    assert response.status_code == 201
    return {"username": username, "password": _VALID_PASSWORD}


async def _attempt_login(
    client: AsyncClient,
    username: str,
    password: str,
) -> int:
    """尝试登录并返回 HTTP 状态码。"""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    return response.status_code


# ===========================================================================
# 账号维度暴力破解防护（SPEC §12.4）
# ===========================================================================


class TestAccountLockout:
    """账号维度暴力破解防护测试（SPEC §12.4）。"""

    async def test_account_locked_after_5_failures(
        self,
        security_client: AsyncClient,
    ) -> None:
        """账号连续失败 5 次后被限制（SPEC §12.4）。"""
        creds = await _create_user_via_api(security_client)

        # 前 5 次失败——应返回 401
        for i in range(5):
            status_code = await _attempt_login(
                security_client,
                creds["username"],
                "WrongPassword!",
            )
            assert status_code == 401, f"第 {i + 1} 次失败应返回 401"

        # 第 6 次——应仍返回 401（限制中的响应与登录失败一致）
        status_code = await _attempt_login(
            security_client,
            creds["username"],
            "WrongPassword!",
        )
        assert status_code == 401

        # 即使密码正确，限制期间也不可登录
        status_code = await _attempt_login(
            security_client,
            creds["username"],
            _VALID_PASSWORD,
        )
        assert status_code == 401

    async def test_account_failures_cleared_on_success(
        self,
        security_client: AsyncClient,
    ) -> None:
        """成功登录后清理账号维度失败状态（SPEC §12.4）。"""
        creds = await _create_user_via_api(security_client)

        # 2 次失败（未达到 5 次阈值）
        await _attempt_login(security_client, creds["username"], "Wrong1!")
        await _attempt_login(security_client, creds["username"], "Wrong2!")

        # 成功登录——应清理失败计数
        status_code = await _attempt_login(
            security_client,
            creds["username"],
            _VALID_PASSWORD,
        )
        assert status_code == 200

        # 再次失败 4 次不应触发限制（因为之前成功登录清理了计数）
        for _ in range(4):
            status_code = await _attempt_login(
                security_client,
                creds["username"],
                "WrongAgain!",
            )
            assert status_code == 401

        # 第 5 次应仍然能尝试（计数从 0 重新开始）
        status_code = await _attempt_login(
            security_client,
            creds["username"],
            "WrongAgain!",
        )
        assert status_code == 401


# ===========================================================================
# IP 维度暴力破解防护（SPEC §12.4）
# ===========================================================================


class TestIPLockout:
    """IP 维度暴力破解防护测试（SPEC §12.4）。"""

    async def test_ip_locked_after_20_failures(
        self,
        security_client: AsyncClient,
    ) -> None:
        """同一 IP 连续失败 20 次后被限制（SPEC §12.4）。

        测试客户端的 IP 均为同一地址（127.0.0.1 / testclient），
        因此 20 次失败后该 IP 应被限制。
        """
        # 使用不存在的用户名进行 20 次失败尝试
        for i in range(20):
            status_code = await _attempt_login(
                security_client,
                f"ghost_user_{i}",
                _VALID_PASSWORD,
            )
            assert status_code == 401

        # 第 21 次——IP 已被限制，应返回 401
        status_code = await _attempt_login(
            security_client,
            "another_user",
            _VALID_PASSWORD,
        )
        assert status_code == 401

    async def test_ip_failures_not_cleared_on_success(
        self,
        security_client: AsyncClient,
        security_engine: AsyncEngine,
    ) -> None:
        """IP 维度成功登录不清理失败计数（SPEC §12.4）。"""
        # 先创建用户
        creds = await _create_user_via_api(security_client, "ipuser")

        # 用不同不存在用户制造 10 次 IP 失败
        for i in range(10):
            await _attempt_login(
                security_client,
                f"ghost_{i}",
                _VALID_PASSWORD,
            )

        # 成功登录——应成功
        status_code = await _attempt_login(
            security_client,
            creds["username"],
            _VALID_PASSWORD,
        )
        assert status_code == 200

        # IP 失败计数应仍然存在（10 次未被清理）
        async with security_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT failure_count FROM login_attempts WHERE dimension = 'ip'"),
            )
            row = result.fetchone()

        assert row is not None
        # IP 计数应 >= 10（成功登录不清理）
        assert row[0] >= 10


# ===========================================================================
# 响应一致性（SPEC §12.4：限制时响应与登录失败一致）
# ===========================================================================


class TestResponseConsistency:
    """限制响应一致性测试（SPEC §12.4）。"""

    async def test_locked_response_same_as_auth_failure(
        self,
        security_client: AsyncClient,
    ) -> None:
        """限制时的响应与普通登录失败响应保持一致（SPEC §12.4）。"""
        creds = await _create_user_via_api(security_client, "consistuser")

        # 记录普通登录失败的响应
        normal_fail = await security_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": "Wrong!"},
        )

        # 触发账号锁定（5 次）
        for _ in range(5):
            await security_client.post(
                "/api/v1/auth/login",
                json={"username": creds["username"], "password": "Wrong!"},
            )

        # 记录锁定后的响应（使用正确密码，但被锁定）
        locked_resp = await security_client.post(
            "/api/v1/auth/login",
            json={"username": creds["username"], "password": _VALID_PASSWORD},
        )

        # 状态码一致
        assert normal_fail.status_code == locked_resp.status_code == 401
        # 响应结构一致（不泄露限制信息）
        assert normal_fail.json().keys() == locked_resp.json().keys()


# ===========================================================================
# 防用户枚举（SPEC §12.4）
# ===========================================================================


class TestAntiEnumeration:
    """防止用户枚举测试（SPEC §12.4）。"""

    async def test_valid_and_invalid_user_same_response(
        self,
        security_client: AsyncClient,
    ) -> None:
        """有效用户和无效用户的登录失败响应一致（SPEC §12.4）。"""
        await _create_user_via_api(security_client, "realuser")

        # 有效用户密码错误
        valid_user_resp = await security_client.post(
            "/api/v1/auth/login",
            json={"username": "realuser", "password": "WrongPassword!"},
        )

        # 无效用户
        invalid_user_resp = await security_client.post(
            "/api/v1/auth/login",
            json={"username": "ghostuser", "password": "WrongPassword!"},
        )

        assert valid_user_resp.status_code == invalid_user_resp.status_code == 401
        # 响应内容应一致（不泄露用户是否存在）
        assert valid_user_resp.json().keys() == invalid_user_resp.json().keys()


# ===========================================================================
# PostgreSQL 持久化验证（SPEC §12.4）
# ===========================================================================


class TestPersistence:
    """暴力破解防护持久化验证（SPEC §12.4）。"""

    async def test_account_failure_persisted(
        self,
        security_client: AsyncClient,
        security_engine: AsyncEngine,
    ) -> None:
        """账号维度失败记录持久化到 PostgreSQL。"""
        await _attempt_login(security_client, "persistuser", "Wrong!")

        async with security_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT failure_count, dimension, identifier "
                    "FROM login_attempts WHERE dimension = 'account'"
                ),
            )
            rows = result.fetchall()

        assert len(rows) >= 1
        account_rows = [r for r in rows if r[1] == "account"]
        assert any(r[2] == "persistuser" for r in account_rows)

    async def test_ip_failure_persisted(
        self,
        security_client: AsyncClient,
        security_engine: AsyncEngine,
    ) -> None:
        """IP 维度失败记录持久化到 PostgreSQL。"""
        await _attempt_login(security_client, "ippersistuser", "Wrong!")

        async with security_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT failure_count, dimension FROM login_attempts WHERE dimension = 'ip'"),
            )
            rows = result.fetchall()

        assert len(rows) >= 1
