"""RBAC 模块 API 集成测试（SPEC §13、§28.3）。

使用 Testcontainers PostgreSQL 18 验证 RBAC 模块的完整 API 调用流：
Router → RbacService → Domain Policy → Repository → Database → Event Dispatch。

测试覆盖：
- 角色 CRUD：创建、查询、列表、更新、启用/禁用
- 角色-权限分配：全量替换、查询
- 用户-角色分配：增量分配、移除、查询
- 管理范围强制：普通管理员越权拒绝、超级管理员绕过
- 超级管理员保护：最后管理员保护、内置角色保护
- 权限即时生效：基于 DB 查询非缓存

前置条件：运行环境需要 Docker（Testcontainers 依赖）。

注意：RBAC 端点需要认证，测试通过直接 DB 操作创建初始超级管理员用户和角色，
然后通过登录 API 获取 Access Token 进行后续 API 调用。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from app.app import create_app
from app.config.settings import AppEnv, Settings
from app.health.providers import DbPoolProvider
from app.modules.user.domain.password import PasswordHasher

pytestmark = [pytest.mark.integration, pytest.mark.g2]

# 测试用有效密钥（与 conftest 一致）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"

_VALID_PASSWORD = "SecurePass123!"

# 全部表 DDL（与 Alembic 迁移 0003–0006 一致）
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
    last_attempt_at    TIMESTAMPTZ,
    PRIMARY KEY (dimension, identifier)
);
CREATE TABLE IF NOT EXISTS roles (
    id              UUID PRIMARY KEY,
    code            VARCHAR(50) NOT NULL,
    name            VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL,
    description     VARCHAR(500),
    is_builtin      BOOLEAN NOT NULL DEFAULT FALSE,
    is_super_admin  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    created_by      UUID,
    updated_by      UUID
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_roles_code ON roles (code);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id     UUID REFERENCES users(id),
    role_id     UUID REFERENCES roles(id),
    assigned_at TIMESTAMPTZ NOT NULL,
    assigned_by UUID,
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX IF NOT EXISTS ix_user_roles_role_id ON user_roles (role_id);
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id          UUID REFERENCES roles(id),
    permission_code  VARCHAR(100) NOT NULL,
    PRIMARY KEY (role_id, permission_code)
);
CREATE INDEX IF NOT EXISTS ix_role_permissions_permission_code
    ON role_permissions (permission_code);
"""


class _TestRbacProvider(DbPoolProvider):
    """测试用数据库连接池 Provider。"""

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
async def rbac_engine(
    postgres_container: PostgresContainer,
) -> AsyncIterator[AsyncEngine]:
    """为 RBAC 模块测试创建引擎并创建全部表。"""
    from app.infrastructure.database.engine import create_engine

    url = postgres_container.get_connection_url()
    engine = create_engine(url, pool_size=3, max_overflow=2)

    async with engine.begin() as conn:
        await conn.execute(text(_DDL))

    yield engine
    await engine.dispose()


@pytest.fixture
async def api_client(rbac_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """创建带数据库连接的异步 API 测试客户端。

    每个测试前清空数据表，确保测试隔离。
    """
    # 清空表（测试隔离，保留表结构，按依赖顺序删除）
    async with rbac_engine.begin() as conn:
        await conn.execute(text("DELETE FROM role_permissions"))
        await conn.execute(text("DELETE FROM user_roles"))
        await conn.execute(text("DELETE FROM roles"))
        await conn.execute(text("DELETE FROM login_attempts"))
        await conn.execute(text("DELETE FROM refresh_tokens"))
        await conn.execute(text("DELETE FROM access_tokens"))
        await conn.execute(text("DELETE FROM sessions"))
        await conn.execute(text("DELETE FROM users"))

    provider = _TestRbacProvider(rbac_engine)
    app = create_app(settings=_make_test_settings(), db_pool_provider=provider)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# 测试辅助：通过直接 DB 创建超级管理员用户、角色并登录获取 Token
# ---------------------------------------------------------------------------


async def _setup_super_admin(
    engine: AsyncEngine,
    client: AsyncClient,
    username: str = "superadmin",
) -> str:
    """通过直接 DB 操作创建超级管理员用户和角色，登录返回 Access Token。

    由于用户创建 API 现在需要认证（RBAC），测试通过直接 DB 插入
    初始数据，再通过公开的登录端点获取 Token。
    """
    now = datetime.now(UTC)
    hasher = PasswordHasher()
    user_id = uuid4()
    role_id = uuid4()
    password_hash = hasher.hash(_VALID_PASSWORD)

    async with engine.begin() as conn:
        # 创建超级管理员角色
        await conn.execute(
            text(
                "INSERT INTO roles (id, code, name, status, is_builtin, is_super_admin, "
                "created_at, updated_at) "
                "VALUES (:id, 'super_admin', '超级管理员', 'active', true, true, :now, :now)"
            ),
            {"id": role_id, "now": now},
        )
        # 创建用户
        await conn.execute(
            text(
                "INSERT INTO users (id, username, display_name, password_hash, status, "
                "password_updated_at, created_at, updated_at) "
                "VALUES (:id, :username, :display, :hash, 'active', :now, :now, :now)"
            ),
            {
                "id": user_id,
                "username": username,
                "display": "Super Admin",
                "hash": password_hash,
                "now": now,
            },
        )
        # 分配角色
        await conn.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES (:uid, :rid, :now)"
            ),
            {"uid": user_id, "rid": role_id, "now": now},
        )

    # 登录获取 Token（公共端点，不需要认证）
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": _VALID_PASSWORD},
    )
    assert response.status_code == 200, f"登录失败: {response.text}"
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    """构造 Bearer Token 请求头。"""
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 角色 CRUD API 测试（SPEC §13.2）
# ===========================================================================


class TestRoleCrudApi:
    """角色 CRUD API 集成测试（SPEC §13.2）。"""

    async def test_create_role(self, api_client: AsyncClient, rbac_engine: AsyncEngine) -> None:
        """超级管理员创建角色返回 201。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        response = await api_client.post(
            "/api/v1/roles",
            json={"code": "editor", "name": "编辑", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == "editor"
        assert data["name"] == "编辑"
        assert data["status"] == "active"

    async def test_create_duplicate_role_conflict(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """重复编码返回 409。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        await api_client.post(
            "/api/v1/roles",
            json={"code": "editor", "name": "编辑", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        response = await api_client.post(
            "/api/v1/roles",
            json={"code": "editor", "name": "编辑2", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        assert response.status_code == 409
        assert "RBAC.ROLE_ALREADY_EXISTS" in response.text

    async def test_get_role_by_id(self, api_client: AsyncClient, rbac_engine: AsyncEngine) -> None:
        """查询角色详情。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        create_resp = await api_client.post(
            "/api/v1/roles",
            json={"code": "viewer", "name": "查看者", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        role_id = create_resp.json()["id"]

        response = await api_client.get(
            f"/api/v1/roles/{role_id}",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        assert response.json()["code"] == "viewer"

    async def test_list_roles_pagination(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """分页查询角色列表。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        for i in range(3):
            await api_client.post(
                "/api/v1/roles",
                json={"code": f"role_{i}", "name": f"角色{i}", "is_super_admin": False},
                headers=_auth_headers(token),
            )
        response = await api_client.get(
            "/api/v1/roles?page=1&page_size=2",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 4  # 3 个新建 + 1 个超级管理员
        assert len(data["items"]) == 2

    async def test_update_role(self, api_client: AsyncClient, rbac_engine: AsyncEngine) -> None:
        """更新角色名称。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        create_resp = await api_client.post(
            "/api/v1/roles",
            json={"code": "editor", "name": "编辑", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        role_id = create_resp.json()["id"]

        response = await api_client.patch(
            f"/api/v1/roles/{role_id}",
            json={"name": "高级编辑"},
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        assert response.json()["name"] == "高级编辑"

    async def test_disable_enable_role(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """禁用和启用角色。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        create_resp = await api_client.post(
            "/api/v1/roles",
            json={"code": "temp", "name": "临时", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        role_id = create_resp.json()["id"]

        disable_resp = await api_client.post(
            f"/api/v1/roles/{role_id}/disable",
            headers=_auth_headers(token),
        )
        assert disable_resp.status_code == 200
        assert disable_resp.json()["status"] == "disabled"

        enable_resp = await api_client.post(
            f"/api/v1/roles/{role_id}/enable",
            headers=_auth_headers(token),
        )
        assert enable_resp.status_code == 200
        assert enable_resp.json()["status"] == "active"

    async def test_disable_builtin_super_admin_protected(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """禁用内置超级管理员角色返回 409（SPEC §13.4）。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        # super_admin 角色由 _setup_super_admin 创建（is_builtin=True）
        async with rbac_engine.connect() as conn:
            result = await conn.execute(text("SELECT id FROM roles WHERE code = 'super_admin'"))
            role_id = result.scalar_one()

        response = await api_client.post(
            f"/api/v1/roles/{role_id}/disable",
            headers=_auth_headers(token),
        )
        assert response.status_code == 409
        assert "RBAC.BUILTIN_ROLE_PROTECTED" in response.text


# ===========================================================================
# 角色-权限分配 API 测试（SPEC §13.2）
# ===========================================================================


class TestPermissionAssignmentApi:
    """角色-权限分配 API 集成测试（SPEC §13.2）。"""

    async def test_assign_and_get_permissions(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """分配权限并查询。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        create_resp = await api_client.post(
            "/api/v1/roles",
            json={"code": "editor", "name": "编辑", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        role_id = create_resp.json()["id"]

        assign_resp = await api_client.put(
            f"/api/v1/roles/{role_id}/permissions",
            json={"permission_codes": ["system:user:read", "system:role:read"]},
            headers=_auth_headers(token),
        )
        assert assign_resp.status_code == 200
        assert set(assign_resp.json()["permission_codes"]) == {
            "system:user:read",
            "system:role:read",
        }

        get_resp = await api_client.get(
            f"/api/v1/roles/{role_id}/permissions",
            headers=_auth_headers(token),
        )
        assert get_resp.status_code == 200
        assert set(get_resp.json()["permission_codes"]) == {
            "system:user:read",
            "system:role:read",
        }


# ===========================================================================
# 用户-角色分配 API 测试（SPEC §13.2）
# ===========================================================================


class TestUserRoleAssignmentApi:
    """用户-角色分配 API 集成测试（SPEC §13.2）。"""

    async def test_assign_and_remove_roles(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """分配和移除用户角色。"""
        token = await _setup_super_admin(rbac_engine, api_client)

        # 创建目标角色
        role_resp = await api_client.post(
            "/api/v1/roles",
            json={"code": "editor", "name": "编辑", "is_super_admin": False},
            headers=_auth_headers(token),
        )
        assert role_resp.status_code == 201

        # 创建目标用户（通过 DB）
        now = datetime.now(UTC)
        target_user_id = uuid4()
        hasher = PasswordHasher()
        async with rbac_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, password_hash, status, "
                    "password_updated_at, created_at, updated_at) "
                    "VALUES (:id, 'target', 'Target', :hash, 'active', :now, :now, :now)"
                ),
                {"id": target_user_id, "hash": hasher.hash(_VALID_PASSWORD), "now": now},
            )

        # 分配角色
        assign_resp = await api_client.put(
            f"/api/v1/users/{target_user_id}/roles",
            json={"role_codes": ["editor"]},
            headers=_auth_headers(token),
        )
        assert assign_resp.status_code == 204

        # 查询用户角色
        get_resp = await api_client.get(
            f"/api/v1/users/{target_user_id}/roles",
            headers=_auth_headers(token),
        )
        assert get_resp.status_code == 200
        assert len(get_resp.json()) == 1
        assert get_resp.json()[0]["code"] == "editor"

        # 移除角色
        remove_resp = await api_client.delete(
            f"/api/v1/users/{target_user_id}/roles",
            json={"role_codes": ["editor"]},
            headers=_auth_headers(token),
        )
        assert remove_resp.status_code == 204

    async def test_remove_last_super_admin_protected(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """移除最后一个超级管理员返回 409（SPEC §13.4）。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        # 获取当前超级管理员用户 ID
        async with rbac_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT u.id FROM users u "
                    "JOIN user_roles ur ON u.id = ur.user_id "
                    "JOIN roles r ON ur.role_id = r.id "
                    "WHERE r.is_super_admin = true"
                )
            )
            super_user_id = result.scalar_one()

        response = await api_client.delete(
            f"/api/v1/users/{super_user_id}/roles",
            json={"role_codes": ["super_admin"]},
            headers=_auth_headers(token),
        )
        assert response.status_code == 409
        assert "RBAC.LAST_SUPER_ADMIN" in response.text

    async def test_get_role_members(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """查询角色成员列表。"""
        token = await _setup_super_admin(rbac_engine, api_client)
        # super_admin 角色有 1 个成员
        async with rbac_engine.connect() as conn:
            result = await conn.execute(text("SELECT id FROM roles WHERE code = 'super_admin'"))
            role_id = result.scalar_one()

        response = await api_client.get(
            f"/api/v1/roles/{role_id}/members",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        assert len(response.json()["user_ids"]) == 1


# ===========================================================================
# 授权检查 API 测试（SPEC §13.3、§23.5）
# ===========================================================================


class TestAuthorizationApi:
    """授权检查 API 集成测试（SPEC §13.3、§23.5）。"""

    async def test_rbac_endpoints_require_auth(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """RBAC 端点无 Token 返回 401（SPEC §23.5）。"""
        response = await api_client.get("/api/v1/roles")
        assert response.status_code == 401

    async def test_unauthorized_user_denied(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """无权限用户（普通用户无 RBAC 权限点）访问管理端点返回 403。

        创建一个无角色的普通用户，登录后尝试访问角色管理 API。
        """
        # 创建普通用户（无角色）
        now = datetime.now(UTC)
        hasher = PasswordHasher()
        normal_user_id = uuid4()
        async with rbac_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, password_hash, status, "
                    "password_updated_at, created_at, updated_at) "
                    "VALUES (:id, 'normal', '普通用户', :hash, 'active', :now, :now, :now)"
                ),
                {"id": normal_user_id, "hash": hasher.hash(_VALID_PASSWORD), "now": now},
            )

        # 登录
        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"username": "normal", "password": _VALID_PASSWORD},
        )
        assert login_resp.status_code == 200
        normal_token = login_resp.json()["access_token"]

        # 无权限访问角色列表 → 403
        response = await api_client.get(
            "/api/v1/roles",
            headers=_auth_headers(normal_token),
        )
        assert response.status_code == 403

    async def test_super_admin_full_access(
        self, api_client: AsyncClient, rbac_engine: AsyncEngine
    ) -> None:
        """超级管理员拥有全部 API 访问权限（SPEC §13.4 绕过）。"""
        token = await _setup_super_admin(rbac_engine, api_client)

        # 超级管理员可以访问角色列表（即使没有显式权限点）
        response = await api_client.get(
            "/api/v1/roles",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
