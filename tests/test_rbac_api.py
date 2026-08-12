"""RBAC 模块 API 契约测试 — SPEC 13.2 / 28.4.

覆盖验收标准（SPEC 34.2）:
  - 角色 CRUD 返回正确状态码和响应结构。
  - 权限点分配全量替换。
  - 用户角色分配和移除。
  - 分页查询角色和成员。
  - 内置角色保护和错误码。

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
    """清理 RBAC、用户和审计相关表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            for table in (
                "rbac_user_roles",
                "rbac_role_permissions",
                "rbac_permissions",
                "rbac_roles",
                "audit_logs",
                "users",
            ):
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


async def _create_test_user(
    database_url: str,
    *,
    username: str = "rbacuser",
) -> str:
    """创建测试用户，返回用户 ID 字符串。"""

    from app.core.security.password import Argon2Hasher

    hasher = Argon2Hasher()
    password_hash = hasher.hash("secure_password_12")
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
                    "VALUES (:id, :u, :d, :p, 'active', "
                    "NULL, NULL, NULL, :t, :t, :t, NULL, NULL)",
                ),
                {
                    "id": str(user_id),
                    "u": username,
                    "d": username.title(),
                    "p": password_hash,
                    "t": now,
                },
            )
    finally:
        await engine.dispose()
    return str(user_id)


_TEST_ACTOR_ID = "00000000-0000-0000-0000-000000000001"


async def _seed_super_admin_actor(database_url: str) -> None:
    """为测试操作者创建超管角色并分配，使 Use Case 内部二次校验通过。

    SPEC 13.3: 关键写 Use Case 在 UoW 中重新读取操作者授权关系。
    仅覆盖入口级依赖不够，需在数据库中准备真实授权数据。
    """

    from app.core.security.authorization import SUPER_ADMIN_ROLE_CODE

    role_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            # 创建内置超管角色
            await conn.execute(
                text(
                    "INSERT INTO rbac_roles "
                    "(id, code, display_name, description, status, "
                    "is_builtin, sort_order, created_at, updated_at) "
                    "VALUES (:id, :code, :dn, '', 'active', true, 0, :t, :t)",
                ),
                {
                    "id": str(role_id),
                    "code": SUPER_ADMIN_ROLE_CODE,
                    "dn": "超级管理员",
                    "t": now,
                },
            )
            # 创建操作者用户
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, "
                    "password_hash, status, created_at, updated_at) "
                    "VALUES (:id, :u, :dn, :ph, 'active', :t, :t) "
                    "ON CONFLICT (id) DO NOTHING",
                ),
                {
                    "id": _TEST_ACTOR_ID,
                    "u": "test_actor",
                    "dn": "Test Actor",
                    "ph": "$argon2id$fake",
                    "t": now,
                },
            )
            # 分配超管角色
            await conn.execute(
                text(
                    "INSERT INTO rbac_user_roles "
                    "(user_id, role_id, created_at, created_by) "
                    "VALUES (:uid, :rid, :t, NULL)",
                ),
                {
                    "uid": _TEST_ACTOR_ID,
                    "rid": str(role_id),
                    "t": now,
                },
            )
    finally:
        await engine.dispose()


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


_SUPER_ADMIN_CTX = UseCaseContext(
    request_id="test-admin-req",
    actor_id="00000000-0000-0000-0000-000000000001",
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
    """创建带 RBAC 模块和超管权限的 TestClient。"""

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


def _create_role(
    client: TestClient,
    *,
    code: str = "editor",
    display_name: str = "编辑者",
) -> dict[str, object]:
    """通过 API 创建角色并返回响应体。"""

    response = client.post(
        "/api/v1/roles",
        json={"code": code, "displayName": display_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_permission(database_url: str, code: str) -> str:
    """直接在数据库中创建权限点，返回权限 ID。"""

    perm_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_permissions (id, code, display_name, "
                    "description, module_code, is_active, created_at, updated_at) "
                    "VALUES (:id, :code, :dn, '', 'test', true, :t, :t)",
                ),
                {
                    "id": str(perm_id),
                    "code": code,
                    "dn": code,
                    "t": now,
                },
            )
    finally:
        await engine.dispose()
    return str(perm_id)


# ═══════════════════════════════════════════════════════════════════════════════
# 角色管理
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_create_role_returns_201_with_location(
    api_client: TestClient,
) -> None:
    """创建角色返回 201 + Location — SPEC 9.3 / 13.2."""

    response = api_client.post(
        "/api/v1/roles",
        json={"code": "manager", "displayName": "管理者"},
    )
    assert response.status_code == 201
    assert "Location" in response.headers
    role_id = response.json()["id"]
    assert response.headers["Location"] == f"/api/v1/roles/{role_id}"
    assert response.json()["code"] == "manager"


@pytest.mark.g2
@pytest.mark.api
def test_create_duplicate_role_returns_409(api_client: TestClient) -> None:
    """重复角色编码返回 409 — SPEC 8.4."""

    _create_role(api_client, code="dup")
    response = api_client.post(
        "/api/v1/roles",
        json={"code": "dup", "displayName": "重复"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "RBAC.ROLE_ALREADY_EXISTS"


@pytest.mark.g2
@pytest.mark.api
def test_list_roles_pagination(api_client: TestClient) -> None:
    """分页查询角色列表 — SPEC 9.4."""

    for i in range(3):
        _create_role(api_client, code=f"role_{i}", display_name=f"角色{i}")

    response = api_client.get(
        "/api/v1/roles",
        params={"page": 1, "pageSize": 2},
    )
    assert response.status_code == 200
    body = response.json()
    # 内置 super_admin + 3 个新角色（超管角色在种子中创建）
    assert body["total"] >= 3
    assert body["page"] == 1
    assert body["pageSize"] == 2


@pytest.mark.g2
@pytest.mark.api
def test_list_roles_sorting(api_client: TestClient) -> None:
    """排序查询角色 — SPEC 9.4."""

    for name in ["c_role", "a_role", "b_role"]:
        _create_role(api_client, code=name, display_name=name)

    response = api_client.get(
        "/api/v1/roles",
        params={"sort": "code", "pageSize": 50},
    )
    assert response.status_code == 200
    codes = [r["code"] for r in response.json()["items"]]
    # super_admin 在最前，然后 a_role, b_role, c_role
    assert codes.index("a_role") < codes.index("b_role")
    assert codes.index("b_role") < codes.index("c_role")


@pytest.mark.g2
@pytest.mark.api
def test_list_roles_status_filter(api_client: TestClient) -> None:
    """按状态筛选角色 — SPEC 9.4."""

    _create_role(api_client, code="active1")
    role2 = _create_role(api_client, code="inactive1")

    # 禁用第二个角色
    api_client.post(f"/api/v1/roles/{role2['id']}/disable")

    response = api_client.get(
        "/api/v1/roles",
        params={"status": "disabled"},
    )
    assert response.status_code == 200
    body = response.json()
    assert all(r["status"] == "disabled" for r in body["items"])
    assert body["total"] == 1


@pytest.mark.g2
@pytest.mark.api
def test_get_role_detail(api_client: TestClient) -> None:
    """查询角色详情 — SPEC 13.2."""

    role = _create_role(api_client, code="detail_role")
    response = api_client.get(f"/api/v1/roles/{role['id']}")
    assert response.status_code == 200
    assert response.json()["code"] == "detail_role"
    assert "permissionCodes" in response.json()


@pytest.mark.g2
@pytest.mark.api
def test_get_nonexistent_role_returns_404(api_client: TestClient) -> None:
    """查询不存在角色返回 404 — SPEC 10.1."""

    response = api_client.get(f"/api/v1/roles/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "RBAC.ROLE_NOT_FOUND"


@pytest.mark.g2
@pytest.mark.api
def test_update_role(api_client: TestClient) -> None:
    """更新角色 — SPEC 13.2."""

    role = _create_role(api_client, code="upd_role")
    response = api_client.put(
        f"/api/v1/roles/{role['id']}",
        json={"displayName": "更新后的名字"},
    )
    assert response.status_code == 200
    assert response.json()["displayName"] == "更新后的名字"


@pytest.mark.g2
@pytest.mark.api
def test_enable_disable_role(api_client: TestClient) -> None:
    """启用和禁用角色 — SPEC 13.2."""

    role = _create_role(api_client, code="toggle_role")

    # 禁用
    response = api_client.post(f"/api/v1/roles/{role['id']}/disable")
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"

    # 启用
    response = api_client.post(f"/api/v1/roles/{role['id']}/enable")
    assert response.status_code == 200
    assert response.json()["status"] == "active"


@pytest.mark.g2
@pytest.mark.api
def test_delete_role_returns_204(api_client: TestClient) -> None:
    """删除角色返回 204 — SPEC 13.2."""

    role = _create_role(api_client, code="del_role")
    response = api_client.delete(f"/api/v1/roles/{role['id']}")
    assert response.status_code == 204


@pytest.mark.g2
@pytest.mark.api
def test_assign_permissions_to_role(
    api_client: TestClient,
    migrated_database_url: str,
) -> None:
    """为角色分配权限点 — SPEC 13.2."""

    role = _create_role(api_client, code="perm_role")
    asyncio.run(_seed_super_admin_actor(migrated_database_url))
    asyncio.run(_create_permission(migrated_database_url, "system:user:read"))
    asyncio.run(_create_permission(migrated_database_url, "system:user:write"))

    response = api_client.put(
        f"/api/v1/roles/{role['id']}/permissions",
        json={"permissionCodes": ["system:user:read", "system:user:write"]},
    )
    assert response.status_code == 200
    assert set(response.json()["permissionCodes"]) == {
        "system:user:read",
        "system:user:write",
    }


@pytest.mark.g2
@pytest.mark.api
def test_get_role_members_empty(
    api_client: TestClient,
) -> None:
    """查询无成员角色的成员列表 — SPEC 13.2."""

    role = _create_role(api_client, code="empty_role")
    response = api_client.get(f"/api/v1/roles/{role['id']}/members")
    assert response.status_code == 200
    assert response.json()["total"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 用户角色管理
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.api
def test_assign_and_remove_user_roles(
    api_client: TestClient,
    migrated_database_url: str,
) -> None:
    """分配和移除用户角色 — SPEC 13.2."""

    user_id = asyncio.run(_create_test_user(migrated_database_url))
    asyncio.run(_seed_super_admin_actor(migrated_database_url))
    role = _create_role(api_client, code="user_role")

    # 分配角色
    response = api_client.put(
        f"/api/v1/users/{user_id}/roles",
        json={"roleCodes": ["user_role"]},
    )
    assert response.status_code == 200
    assert len(response.json()["role_ids"]) == 1

    # 查询用户角色
    response = api_client.get(f"/api/v1/users/{user_id}/roles")
    assert response.status_code == 200
    assert len(response.json()["role_ids"]) == 1

    # 移除角色
    response = api_client.delete(
        f"/api/v1/users/{user_id}/roles/{role['id']}",
    )
    assert response.status_code == 204

    # 再次查询——角色已移除
    response = api_client.get(f"/api/v1/users/{user_id}/roles")
    assert response.status_code == 200
    assert len(response.json()["role_ids"]) == 0


@pytest.mark.g2
@pytest.mark.api
def test_get_user_roles_nonexistent_returns_404(
    api_client: TestClient,
) -> None:
    """查询不存在用户的角色返回 404 — SPEC 10.1."""

    response = api_client.get(f"/api/v1/users/{uuid4()}/roles")
    assert response.status_code == 404
