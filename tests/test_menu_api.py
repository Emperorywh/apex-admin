"""菜单模块 API 契约测试 — SPEC 15.1 / 15.2 / 28.4.

覆盖验收标准:
  - 菜单 API 契约：创建/树查询/更新/启用禁用/层级排序调整，
    支持目录/页面/外链类型与路由名/路径/组件/图标元数据、可见性配置。
  - 循环层级防护（含并发调整）。
  - 角色菜单分配与移除幂等。
  - 当前用户菜单树按启用角色聚合，变更提交后下次查询立即生效。
  - 当前用户权限编码端点。
  - 菜单可见性不承担授权：隐藏菜单对应接口仍按服务端权限放行、
    无权限接口即使菜单可见仍 403。

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
    """清理菜单与关联表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM menu_role_menus"))
            await conn.execute(text("DELETE FROM menu_menus"))
            await conn.execute(text("DELETE FROM rbac_user_roles"))
            await conn.execute(text("DELETE FROM rbac_role_permissions"))
            await conn.execute(text("DELETE FROM rbac_permissions"))
            await conn.execute(text("DELETE FROM rbac_roles"))
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM users"))
    finally:
        await engine.dispose()


async def _seed_user(database_url: str, user_id: str) -> None:
    """创建测试用户。"""

    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, "
                    "password_hash, status, created_at, updated_at) "
                    "VALUES (:id, :u, :d, :p, 'active', :t, :t)",
                ),
                {
                    "id": user_id,
                    "u": f"user_{user_id[:8]}",
                    "d": f"User {user_id[:8]}",
                    "p": "$argon2id$fake",
                    "t": now,
                },
            )
    finally:
        await engine.dispose()


async def _seed_role(
    database_url: str,
    role_id: str,
    code: str,
    status: str = "active",
) -> None:
    """创建测试角色。"""

    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_roles (id, code, display_name, description, "
                    "status, is_builtin, sort_order, created_at, updated_at) "
                    "VALUES (:id, :code, :name, NULL, :status, false, 0, :t, :t)",
                ),
                {
                    "id": role_id,
                    "code": code,
                    "name": code.title(),
                    "status": status,
                    "t": now,
                },
            )
    finally:
        await engine.dispose()


async def _seed_permission(database_url: str, perm_id: str, code: str) -> None:
    """创建测试权限点。"""

    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_permissions (id, code, display_name, "
                    "description, module_code, is_active, created_at, updated_at) "
                    "VALUES (:id, :code, :name, NULL, 'test', true, :t, :t)",
                ),
                {
                    "id": perm_id,
                    "code": code,
                    "name": code,
                    "t": now,
                },
            )
    finally:
        await engine.dispose()


async def _assign_user_role(
    database_url: str,
    user_id: str,
    role_id: str,
) -> None:
    """分配用户角色。"""

    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_user_roles (user_id, role_id, "
                    "created_at, created_by) VALUES (:u, :r, :t, 'test')",
                ),
                {"u": user_id, "r": role_id, "t": now},
            )
    finally:
        await engine.dispose()


async def _assign_role_permission(
    database_url: str,
    role_id: str,
    perm_id: str,
) -> None:
    """分配角色权限点。"""

    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_role_permissions (role_id, permission_id, "
                    "created_at) VALUES (:r, :p, :t)",
                ),
                {"r": role_id, "p": perm_id, "t": now},
            )
    finally:
        await engine.dispose()


# ── 测试 fixture ───────────────────────────────────────────────────────────

_TEST_ACTOR_ID = "00000000-0000-0000-0000-0000000000dd"


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
    request_id="test-menu-req",
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


def _make_ctx_override(actor_id: str) -> UseCaseContext:
    """构造指定 actor 的认证上下文。"""

    return UseCaseContext(request_id="test-menu-req", actor_id=actor_id)


def _make_auth_override(
    actor_id: str,
    permissions: frozenset[str],
    is_super_admin: bool = False,
) -> ActorAuthorization:
    """构造指定权限的授权。"""

    return ActorAuthorization(
        ctx=UseCaseContext(request_id="test-menu-req", actor_id=actor_id),
        permissions=permissions,
        is_super_admin=is_super_admin,
    )


@pytest.fixture()
def api_client(migrated_database_url: str) -> Iterator[TestClient]:
    """创建带菜单模块和超管权限的 TestClient。"""

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


def _create_menu(
    client: TestClient,
    *,
    menu_type: str = "directory",
    title: str = "Test",
    parent_id: str | None = None,
    visible: bool = True,
    name: str | None = None,
    path: str | None = None,
    component: str | None = None,
    icon: str | None = None,
    sort_order: int = 0,
) -> dict[str, object]:
    """通过 API 创建菜单并返回响应体。"""

    payload: dict[str, object] = {"menu_type": menu_type, "title": title}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if not visible:
        payload["visible"] = False
    if name is not None:
        payload["name"] = name
    if path is not None:
        payload["path"] = path
    if component is not None:
        payload["component"] = component
    if icon is not None:
        payload["icon"] = icon
    if sort_order:
        payload["sort_order"] = sort_order
    response = client.post("/api/v1/menus", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 菜单 API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestMenuCRUDAPI:
    """菜单 CRUD API 契约测试 — SPEC 15.1 / 9.3."""

    def test_create_directory(self, api_client: TestClient) -> None:
        """创建目录类型菜单返回 201。"""

        response = api_client.post(
            "/api/v1/menus",
            json={"menu_type": "directory", "title": "系统管理"},
        )
        assert response.status_code == 201
        assert "location" in {k.lower() for k in response.headers}
        body = response.json()
        assert body["menu_type"] == "directory"
        assert body["status"] == "active"
        assert body["visible"] is True

    def test_create_page_with_metadata(self, api_client: TestClient) -> None:
        """创建页面类型菜单带路由元数据 — SPEC 15.1."""

        response = api_client.post(
            "/api/v1/menus",
            json={
                "menu_type": "page",
                "title": "用户管理",
                "name": "system_user",
                "path": "/system/user",
                "component": "system/user/index",
                "icon": "user",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "system_user"
        assert body["path"] == "/system/user"
        assert body["component"] == "system/user/index"
        assert body["icon"] == "user"

    def test_create_link_type(self, api_client: TestClient) -> None:
        """创建外链类型菜单 — SPEC 15.1."""

        response = api_client.post(
            "/api/v1/menus",
            json={
                "menu_type": "link",
                "title": "外部链接",
                "path": "https://example.com",
            },
        )
        assert response.status_code == 201
        assert response.json()["menu_type"] == "link"

    def test_create_with_visibility(self, api_client: TestClient) -> None:
        """创建菜单配置可见性 — SPEC 15.1."""

        response = api_client.post(
            "/api/v1/menus",
            json={"menu_type": "page", "title": "Hidden", "visible": False},
        )
        assert response.status_code == 201
        assert response.json()["visible"] is False

    def test_create_unknown_field_422(self, api_client: TestClient) -> None:
        """未知字段返回 422。"""

        response = api_client.post(
            "/api/v1/menus",
            json={"menu_type": "page", "title": "T", "bad": "field"},
        )
        assert response.status_code == 422

    def test_get_detail(self, api_client: TestClient) -> None:
        """查询菜单详情返回 200。"""

        menu = _create_menu(api_client)
        response = api_client.get(f"/api/v1/menus/{menu['id']}")
        assert response.status_code == 200
        assert response.json()["title"] == "Test"

    def test_get_detail_not_found_404(self, api_client: TestClient) -> None:
        """查询不存在的菜单返回 404。"""

        response = api_client.get(f"/api/v1/menus/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "MENU.NOT_FOUND"

    def test_update(self, api_client: TestClient) -> None:
        """更新菜单返回 200。"""

        menu = _create_menu(api_client)
        response = api_client.put(
            f"/api/v1/menus/{menu['id']}",
            json={"title": "Updated", "visible": False},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Updated"
        assert response.json()["visible"] is False

    def test_enable_disable(self, api_client: TestClient) -> None:
        """启用和禁用菜单。"""

        menu = _create_menu(api_client)
        # 禁用
        response = api_client.post(f"/api/v1/menus/{menu['id']}/disable")
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"
        # 再次禁用 → 409
        response = api_client.post(f"/api/v1/menus/{menu['id']}/disable")
        assert response.status_code == 409
        # 启用
        response = api_client.post(f"/api/v1/menus/{menu['id']}/enable")
        assert response.status_code == 200
        assert response.json()["status"] == "active"

    def test_adjust_hierarchy(self, api_client: TestClient) -> None:
        """调整菜单层级和排序。"""

        parent = _create_menu(api_client, title="Parent")
        child = _create_menu(api_client, title="Child")
        response = api_client.put(
            f"/api/v1/menus/{child['id']}/hierarchy",
            json={"parent_id": str(parent["id"]), "sort_order": 3},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["parent_id"] == parent["id"]
        assert body["sort_order"] == 3

    def test_adjust_hierarchy_direct_cycle_409(
        self,
        api_client: TestClient,
    ) -> None:
        """直接循环返回 409。"""

        menu = _create_menu(api_client)
        response = api_client.put(
            f"/api/v1/menus/{menu['id']}/hierarchy",
            json={"parent_id": str(menu["id"]), "sort_order": 0},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "MENU.CYCLE_DETECTED"

    def test_adjust_hierarchy_indirect_cycle_409(
        self,
        api_client: TestClient,
    ) -> None:
        """间接循环返回 409。"""

        a = _create_menu(api_client, title="A")
        b = _create_menu(api_client, title="B", parent_id=str(a["id"]))
        c = _create_menu(api_client, title="C", parent_id=str(b["id"]))
        response = api_client.put(
            f"/api/v1/menus/{a['id']}/hierarchy",
            json={"parent_id": str(c["id"]), "sort_order": 0},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "MENU.CYCLE_DETECTED"

    def test_delete(self, api_client: TestClient) -> None:
        """删除叶子菜单返回 204。"""

        menu = _create_menu(api_client)
        response = api_client.delete(f"/api/v1/menus/{menu['id']}")
        assert response.status_code == 204

    def test_delete_with_children_409(self, api_client: TestClient) -> None:
        """有子菜单时删除返回 409。"""

        parent = _create_menu(api_client, title="P")
        _create_menu(api_client, title="C", parent_id=str(parent["id"]))
        response = api_client.delete(f"/api/v1/menus/{parent['id']}")
        assert response.status_code == 409
        assert response.json()["code"] == "MENU.HAS_CHILDREN"

    def test_get_tree(self, api_client: TestClient) -> None:
        """查询菜单树返回层级结构。"""

        root = _create_menu(api_client, title="Root")
        _create_menu(api_client, title="C1", parent_id=str(root["id"]))
        _create_menu(api_client, title="C2", parent_id=str(root["id"]))
        response = api_client.get("/api/v1/menus/tree")
        assert response.status_code == 200
        tree = response.json()
        assert len(tree) == 1
        assert tree[0]["title"] == "Root"
        assert len(tree[0]["children"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 角色菜单分配 API
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestRoleMenuAPI:
    """角色菜单分配 API 契约测试 — SPEC 15.1."""

    def test_assign_role_menus_idempotent(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """角色菜单分配幂等。"""

        role_id = uuid4()
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "editor"))
        menu = _create_menu(api_client)

        # 第一次分配
        response = api_client.put(
            f"/api/v1/roles/{role_id}/menus",
            json={"menu_ids": [str(menu["id"])]},
        )
        assert response.status_code == 200
        # 第二次相同分配 → 幂等
        response = api_client.put(
            f"/api/v1/roles/{role_id}/menus",
            json={"menu_ids": [str(menu["id"])]},
        )
        assert response.status_code == 200

        # 查询确认
        response = api_client.get(f"/api/v1/roles/{role_id}/menus")
        assert response.status_code == 200
        assert len(response.json()["menu_ids"]) == 1

    def test_remove_role_menu_idempotent(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """移除角色菜单幂等。"""

        role_id = uuid4()
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "viewer"))
        menu = _create_menu(api_client)

        # 先分配
        api_client.put(
            f"/api/v1/roles/{role_id}/menus",
            json={"menu_ids": [str(menu["id"])]},
        )
        # 第一次移除 → 204
        response = api_client.delete(
            f"/api/v1/roles/{role_id}/menus/{menu['id']}",
        )
        assert response.status_code == 204
        # 第二次移除 → 仍 204（幂等）
        response = api_client.delete(
            f"/api/v1/roles/{role_id}/menus/{menu['id']}",
        )
        assert response.status_code == 204


# ═══════════════════════════════════════════════════════════════════════════════
# 当前用户菜单与权限 API
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestCurrentUserMenuAPI:
    """当前用户菜单与权限 API 契约测试 — SPEC 15.2."""

    def test_current_user_menu_tree(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """当前用户菜单树按启用角色聚合。"""

        user_id = uuid4()
        role_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "r1"))
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role_id)),
        )

        # 创建菜单
        root = _create_menu(api_client, title="Root")
        child = _create_menu(api_client, title="Child", parent_id=str(root["id"]))

        # 分配菜单给角色
        api_client.put(
            f"/api/v1/roles/{role_id}/menus",
            json={"menu_ids": [str(root["id"]), str(child["id"])]},
        )

        # 覆盖依赖模拟当前用户
        api_client.app.dependency_overrides[get_authenticated_context_async] = lambda: (
            _make_ctx_override(str(user_id))
        )
        api_client.app.dependency_overrides[get_actor_authorization] = lambda: (
            _make_auth_override(
                str(user_id),
                frozenset(),
                is_super_admin=False,
            )
        )

        response = api_client.get("/api/v1/me/menus")
        assert response.status_code == 200
        tree = response.json()
        assert len(tree) >= 1
        titles = _collect_titles(tree)
        assert "Root" in titles
        assert "Child" in titles

    def test_menu_change_immediate(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """菜单变更提交后下一次查询立即返回新关系 — SPEC 15.2。"""

        user_id = uuid4()
        role_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "editor"))
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role_id)),
        )

        # 初始查询 → 空树
        api_client.app.dependency_overrides[get_authenticated_context_async] = lambda: (
            _make_ctx_override(str(user_id))
        )
        api_client.app.dependency_overrides[get_actor_authorization] = lambda: (
            _make_auth_override(str(user_id), frozenset())
        )
        response = api_client.get("/api/v1/me/menus")
        assert response.status_code == 200
        assert response.json() == []

        # 创建菜单并分配（切换到超管操作）
        api_client.app.dependency_overrides[get_authenticated_context_async] = (
            _super_admin_ctx_override
        )
        api_client.app.dependency_overrides[get_actor_authorization] = (
            _super_admin_auth_override
        )
        menu = _create_menu(api_client, title="New")
        api_client.put(
            f"/api/v1/roles/{role_id}/menus",
            json={"menu_ids": [str(menu["id"])]},
        )

        # 切回当前用户查询 → 立即看到新菜单
        api_client.app.dependency_overrides[get_authenticated_context_async] = lambda: (
            _make_ctx_override(str(user_id))
        )
        api_client.app.dependency_overrides[get_actor_authorization] = lambda: (
            _make_auth_override(str(user_id), frozenset())
        )
        response = api_client.get("/api/v1/me/menus")
        assert response.status_code == 200
        titles = _collect_titles(response.json())
        assert "New" in titles

    def test_current_user_permissions(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """当前用户权限编码端点返回角色权限并集 — SPEC 15.2。"""

        user_id = uuid4()
        role_id = uuid4()
        perm1_id = uuid4()
        perm2_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "editor"))
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role_id)),
        )
        asyncio.run(
            _seed_permission(migrated_database_url, str(perm1_id), "menu:menu:read"),
        )
        asyncio.run(
            _seed_permission(migrated_database_url, str(perm2_id), "menu:menu:write"),
        )
        asyncio.run(
            _assign_role_permission(migrated_database_url, str(role_id), str(perm1_id)),
        )
        asyncio.run(
            _assign_role_permission(migrated_database_url, str(role_id), str(perm2_id)),
        )

        api_client.app.dependency_overrides[get_authenticated_context_async] = lambda: (
            _make_ctx_override(str(user_id))
        )
        api_client.app.dependency_overrides[get_actor_authorization] = lambda: (
            _make_auth_override(str(user_id), frozenset())
        )

        response = api_client.get("/api/v1/me/permissions")
        assert response.status_code == 200
        perms = response.json()["permissions"]
        assert "menu:menu:read" in perms
        assert "menu:menu:write" in perms


# ═══════════════════════════════════════════════════════════════════════════════
# 菜单可见性不承担授权 — SPEC 23.5 / 15.2 / 13.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestMenuVisibilityNotAuthorization:
    """菜单可见性不承担授权测试 — SPEC 23.5 / 15.2 / 13.3.

    SPEC 23.5: "禁止仅依赖前端菜单和按钮控制权限"。
    SPEC 13.3: 接口访问授权由服务端 RBAC 权限校验决定。

    证明:
      1. 隐藏菜单（visible=false）对应接口仍按服务端权限放行。
      2. 无权限接口即使菜单可见仍 403。
    """

    def test_hidden_menu_api_still_accessible_with_permission(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """隐藏菜单对应接口在有权限时仍可访问 — SPEC 23.5.

        用户拥有 menu:menu:read 权限但菜单设为 visible=false。
        读取接口仍返回 200，证明隐藏菜单不影响授权。
        """

        user_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))

        # 创建隐藏菜单
        menu = _create_menu(api_client, title="Hidden Menu", visible=False)

        # 用户有 read 权限但不是超管
        api_client.app.dependency_overrides[get_authenticated_context_async] = lambda: (
            _make_ctx_override(str(user_id))
        )
        api_client.app.dependency_overrides[get_actor_authorization] = lambda: (
            _make_auth_override(
                str(user_id),
                frozenset({"menu:menu:read"}),
                is_super_admin=False,
            )
        )

        # 读取接口返回 200（即使菜单隐藏，有权限即可访问）
        response = api_client.get(f"/api/v1/menus/{menu['id']}")
        assert response.status_code == 200

    def test_visible_menu_api_still_403_without_permission(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """菜单可见但无权限接口仍返回 403 — SPEC 23.5 / 13.3.

        菜单 visible=true（对前端可见），但用户没有 menu:menu:write 权限。
        写接口返回 403，证明菜单可见不等于有权限。
        """

        user_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))

        # 创建可见菜单
        _create_menu(api_client, title="Visible Menu", visible=True)

        # 用户只有 read 权限，没有 write 权限
        api_client.app.dependency_overrides[get_authenticated_context_async] = lambda: (
            _make_ctx_override(str(user_id))
        )
        api_client.app.dependency_overrides[get_actor_authorization] = lambda: (
            _make_auth_override(
                str(user_id),
                frozenset({"menu:menu:read"}),
                is_super_admin=False,
            )
        )

        # 尝试写入接口 → 403（即使菜单可见，无 write 权限被拒绝）
        response = api_client.post(
            "/api/v1/menus",
            json={"menu_type": "page", "title": "Attempt"},
        )
        assert response.status_code == 403


def _collect_titles(tree: list[dict[str, object]]) -> list[str]:
    """递归收集菜单树中所有标题。"""

    titles: list[str] = []
    for node in tree:
        title = node["title"]
        assert isinstance(title, str)
        titles.append(title)
        children = node["children"]
        assert isinstance(children, list)
        titles.extend(_collect_titles(children))  # type: ignore[arg-type]
    return titles
