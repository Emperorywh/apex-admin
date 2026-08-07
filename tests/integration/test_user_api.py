"""用户模块 API 集成测试（SPEC §11、§28.3）。

使用 Testcontainers PostgreSQL 18 验证用户模块的完整 API 调用流：
Router → Use Case → Domain Policy → Repository → Database → Event Dispatch。

测试覆盖：
- 创建用户、查询详情、分页列表、更新资料、启用/禁用
- 管理员重置密码、用户自助改密
- 自助查询和更新资料
- 密码哈希不在 API 响应中
- 用户名唯一约束
- Argon2id 参数验证

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
from app.infrastructure.database.engine import create_engine

pytestmark = [pytest.mark.integration, pytest.mark.g2]

# 测试用有效密钥（与 conftest 一致）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"

_VALID_PASSWORD = "SecurePass123!"

# users 表 DDL（与 Alembic 迁移 0003_user 一致）
_USERS_TABLE_DDL = """
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
)
"""


class _TestUserProvider(DbPoolProvider):
    """测试用数据库连接池 Provider。

    包装已创建的测试引擎，``initialize`` 和 ``dispose`` 为空操作
    （引擎生命周期由测试 fixture 管理）。
    """

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
async def user_engine(
    postgres_container: PostgresContainer,
) -> AsyncIterator[AsyncEngine]:
    """为用户模块测试创建引擎并创建 users 表。"""
    url = postgres_container.get_connection_url()
    engine = create_engine(url, pool_size=3, max_overflow=2)

    async with engine.begin() as conn:
        await conn.execute(text(_USERS_TABLE_DDL))

    yield engine
    await engine.dispose()


@pytest.fixture
async def api_client(user_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """创建带数据库连接的异步 API 测试客户端。

    每个测试前清空 users 表，确保测试隔离。
    使用 ``ASGITransport`` 直连 ASGI 应用，不经过网络层。
    """
    # 清空 users 表（测试隔离）
    async with user_engine.begin() as conn:
        await conn.execute(text("DELETE FROM users"))

    # 创建应用并注入数据库 provider
    provider = _TestUserProvider(user_engine)
    app = create_app(settings=_make_test_settings(), db_pool_provider=provider)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ===========================================================================
# 表结构验证
# ===========================================================================


class TestUserTableSchema:
    """用户表结构验证（SPEC §11.2）。"""

    async def test_users_table_exists(
        self,
        user_engine: AsyncEngine,
    ) -> None:
        """users 表存在且包含全部必需列。"""
        async with user_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users' ORDER BY ordinal_position"
                )
            )
            columns = {row[0] for row in result.fetchall()}

        required = {
            "id",
            "username",
            "display_name",
            "password_hash",
            "status",
            "phone",
            "email",
            "last_login_at",
            "password_updated_at",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
        assert required.issubset(columns), f"缺失列: {required - columns}"

    async def test_username_unique_constraint(
        self,
        user_engine: AsyncEngine,
    ) -> None:
        """username 列具有唯一约束（SPEC §11.2、§8.3）。"""
        async with user_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'users' AND indexdef LIKE '%UNIQUE%'"
                )
            )
            indexes = [row[0] for row in result.fetchall()]
        assert any("username" in idx for idx in indexes)


# ===========================================================================
# API 测试——管理员操作
# ===========================================================================


class TestUserCrudApi:
    """用户 CRUD API 集成测试。"""

    async def test_create_user_returns_201_without_password_hash(
        self,
        api_client: AsyncClient,
    ) -> None:
        """创建用户返回 201，响应不含 password_hash（SPEC §9.3、§23.2）。"""
        response = await api_client.post(
            "/api/v1/users",
            json={
                "username": "testuser1",
                "display_name": "Test User 1",
                "password": _VALID_PASSWORD,
                "phone": "13800138000",
                "email": "test1@example.com",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "testuser1"
        assert data["display_name"] == "Test User 1"
        assert data["status"] == "active"
        assert data["phone"] == "13800138000"
        assert "password_hash" not in data

    async def test_create_user_rejects_unknown_field(
        self,
        api_client: AsyncClient,
    ) -> None:
        """创建用户拒绝未知字段（SPEC §9.2：extra=forbid）。"""
        response = await api_client.post(
            "/api/v1/users",
            json={
                "username": "testuser2",
                "display_name": "Test User 2",
                "password": _VALID_PASSWORD,
                "extra_field": "not allowed",
            },
        )
        assert response.status_code == 422

    async def test_create_user_short_password_rejected(
        self,
        api_client: AsyncClient,
    ) -> None:
        """短密码被拒绝（SPEC §23.2）。"""
        response = await api_client.post(
            "/api/v1/users",
            json={
                "username": "testuser3",
                "display_name": "Test User 3",
                "password": "short",
            },
        )
        assert response.status_code == 422

    async def test_create_duplicate_username_conflict(
        self,
        api_client: AsyncClient,
    ) -> None:
        """重复用户名返回冲突（SPEC §11.2）。"""
        user_data = {
            "username": "duplicate",
            "display_name": "First",
            "password": _VALID_PASSWORD,
        }
        resp1 = await api_client.post("/api/v1/users", json=user_data)
        assert resp1.status_code == 201

        resp2 = await api_client.post("/api/v1/users", json=user_data)
        assert resp2.status_code == 409
        assert "USER.ALREADY_EXISTS" in resp2.text

    async def test_get_user_by_id(
        self,
        api_client: AsyncClient,
    ) -> None:
        """查询用户详情。"""
        create_resp = await api_client.post(
            "/api/v1/users",
            json={
                "username": "getme",
                "display_name": "Get Me",
                "password": _VALID_PASSWORD,
            },
        )
        user_id = create_resp.json()["id"]

        response = await api_client.get(f"/api/v1/users/{user_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == user_id
        assert data["username"] == "getme"
        assert "password_hash" not in data

    async def test_get_nonexistent_user_returns_404(
        self,
        api_client: AsyncClient,
    ) -> None:
        """查询不存在的用户返回 404。"""
        response = await api_client.get("/api/v1/users/00000000-0000-0000-0000-000000000099")
        assert response.status_code == 404
        assert "USER.NOT_FOUND" in response.text

    async def test_list_users_pagination(
        self,
        api_client: AsyncClient,
    ) -> None:
        """分页查询用户列表。"""
        for i in range(3):
            await api_client.post(
                "/api/v1/users",
                json={
                    "username": f"listuser{i}",
                    "display_name": f"List User {i}",
                    "password": _VALID_PASSWORD,
                },
            )

        response = await api_client.get("/api/v1/users?page=1&page_size=2")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 3
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["items"]) == 2
        assert "pages" in data
        # 列表项不含密码哈希
        for item in data["items"]:
            assert "password_hash" not in item

    async def test_update_user_profile(
        self,
        api_client: AsyncClient,
    ) -> None:
        """更新用户资料。"""
        create_resp = await api_client.post(
            "/api/v1/users",
            json={
                "username": "updateme",
                "display_name": "Original Name",
                "password": _VALID_PASSWORD,
            },
        )
        user_id = create_resp.json()["id"]

        response = await api_client.patch(
            f"/api/v1/users/{user_id}",
            json={"display_name": "Updated Name", "phone": "13900139000"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "Updated Name"
        assert data["phone"] == "13900139000"

    async def test_enable_disable_user(
        self,
        api_client: AsyncClient,
    ) -> None:
        """启用和禁用用户。"""
        create_resp = await api_client.post(
            "/api/v1/users",
            json={
                "username": "enabledisable",
                "display_name": "Enable Disable",
                "password": _VALID_PASSWORD,
            },
        )
        user_id = create_resp.json()["id"]

        disable_resp = await api_client.post(f"/api/v1/users/{user_id}/disable")
        assert disable_resp.status_code == 200
        assert disable_resp.json()["status"] == "disabled"

        enable_resp = await api_client.post(f"/api/v1/users/{user_id}/enable")
        assert enable_resp.status_code == 200
        assert enable_resp.json()["status"] == "active"

    async def test_reset_password_returns_204(
        self,
        api_client: AsyncClient,
    ) -> None:
        """管理员重置密码返回 204。"""
        create_resp = await api_client.post(
            "/api/v1/users",
            json={
                "username": "resetpwd",
                "display_name": "Reset Pwd",
                "password": _VALID_PASSWORD,
            },
        )
        user_id = create_resp.json()["id"]

        response = await api_client.post(
            f"/api/v1/users/{user_id}/reset-password",
            json={"new_password": "BrandNewPass789!"},
        )
        assert response.status_code == 204

    async def test_stored_password_is_argon2id(
        self,
        api_client: AsyncClient,
        user_engine: AsyncEngine,
    ) -> None:
        """数据库中存储的密码哈希使用 Argon2id（SPEC §12.1）。"""
        await api_client.post(
            "/api/v1/users",
            json={
                "username": "hashcheck",
                "display_name": "Hash Check",
                "password": _VALID_PASSWORD,
            },
        )

        async with user_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT password_hash FROM users WHERE username = 'hashcheck'")
            )
            stored_hash = result.scalar_one()

        assert "$argon2id$" in stored_hash
        assert "m=65536" in stored_hash  # SPEC §12.1
        assert "t=3" in stored_hash  # SPEC §12.1
        assert "p=1" in stored_hash  # SPEC §12.1


# ===========================================================================
# API 测试——自助操作
# ===========================================================================


class TestSelfServiceApi:
    """用户自助操作 API 测试。"""

    async def _create_and_get_id(
        self,
        api_client: AsyncClient,
        username: str = "selfsvc",
    ) -> str:
        """创建测试用户并返回 ID。"""
        resp = await api_client.post(
            "/api/v1/users",
            json={
                "username": username,
                "display_name": username.title(),
                "password": _VALID_PASSWORD,
            },
        )
        return resp.json()["id"]

    async def test_get_self_profile(
        self,
        api_client: AsyncClient,
    ) -> None:
        """自助查询资料。"""
        user_id = await self._create_and_get_id(api_client, "selfprofile")

        response = await api_client.get(
            "/api/v1/me",
            headers={"X-User-Id": user_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "selfprofile"
        assert "password_hash" not in data

    async def test_update_self_profile(
        self,
        api_client: AsyncClient,
    ) -> None:
        """自助更新资料。"""
        user_id = await self._create_and_get_id(api_client, "selfupdate")

        response = await api_client.patch(
            "/api/v1/me",
            headers={"X-User-Id": user_id},
            json={"display_name": "Self Updated"},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Self Updated"

    async def test_change_password_returns_204(
        self,
        api_client: AsyncClient,
    ) -> None:
        """自助修改密码返回 204。"""
        user_id = await self._create_and_get_id(api_client, "selfchangepwd")

        response = await api_client.post(
            "/api/v1/me/change-password",
            headers={"X-User-Id": user_id},
            json={
                "current_password": _VALID_PASSWORD,
                "new_password": "BrandNewPass789!",
            },
        )
        assert response.status_code == 204

    async def test_change_password_wrong_current_returns_400(
        self,
        api_client: AsyncClient,
    ) -> None:
        """自助修改密码——当前密码错误返回 400。"""
        user_id = await self._create_and_get_id(api_client, "selfwrongpwd")

        response = await api_client.post(
            "/api/v1/me/change-password",
            headers={"X-User-Id": user_id},
            json={
                "current_password": "WrongCurrent!!",
                "new_password": "BrandNewPass789!",
            },
        )
        assert response.status_code == 400
        assert "USER.INVALID_CREDENTIALS" in response.text
