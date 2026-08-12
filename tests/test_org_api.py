"""组织模块 API 契约测试 — SPEC 14.1 / 28.4.

覆盖验收标准（SPEC 34.3）:
  - 部门 CRUD 返回正确状态码和响应结构。
  - 树查询返回正确的层级结构。
  - 启用禁用返回正确状态。
  - 层级调整和负责人设置。
  - 循环防护和删除保护返回正确错误码。

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
    """清理组织和审计相关表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM org_user_posts"))
            await conn.execute(text("DELETE FROM org_user_departments"))
            await conn.execute(text("DELETE FROM org_posts"))
            await conn.execute(text("DELETE FROM org_departments"))
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM users"))
    finally:
        await engine.dispose()


async def _seed_user(database_url: str, username: str) -> str:
    """创建测试用户，返回用户 ID 字符串。"""

    user_id = uuid4()
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
                    "id": str(user_id),
                    "u": username,
                    "d": username.title(),
                    "p": "$argon2id$fake",
                    "t": now,
                },
            )
    finally:
        await engine.dispose()
    return str(user_id)


_TEST_ACTOR_ID = "00000000-0000-0000-0000-0000000000cc"

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
    request_id="test-org-req",
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


@pytest.fixture()
def api_client(migrated_database_url: str) -> Iterator[TestClient]:
    """创建带 org 模块和超管权限的 TestClient。"""

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


def _create_dept(
    client: TestClient,
    *,
    code: str = "hq",
    display_name: str = "总部",
    parent_id: str | None = None,
) -> dict[str, object]:
    """通过 API 创建部门并返回响应体。"""

    payload: dict[str, object] = {"code": code, "displayName": display_name}
    if parent_id is not None:
        payload["parentId"] = parent_id
    response = client.post("/api/v1/departments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# 部门 CRUD API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestDepartmentCRUDAPI:
    """部门 CRUD API 契约测试 — SPEC 14.1 / 9.3."""

    def test_create_returns_201_with_location(
        self,
        api_client: TestClient,
    ) -> None:
        """创建部门返回 201 和 Location 头（SPEC 9.3）。"""

        response = api_client.post(
            "/api/v1/departments",
            json={"code": "engineering", "displayName": "工程部"},
        )
        assert response.status_code == 201
        assert "location" in {k.lower() for k in response.headers}
        body = response.json()
        assert body["code"] == "engineering"
        assert body["status"] == "active"

    def test_create_duplicate_code_409(
        self,
        api_client: TestClient,
    ) -> None:
        """重复编码返回 409。"""

        _create_dept(api_client, code="dup", display_name="D1")
        response = api_client.post(
            "/api/v1/departments",
            json={"code": "dup", "displayName": "D2"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ORG.DEPT_ALREADY_EXISTS"

    def test_create_unknown_field_422(
        self,
        api_client: TestClient,
    ) -> None:
        """未知字段返回 422。"""

        response = api_client.post(
            "/api/v1/departments",
            json={"code": "test", "displayName": "T", "bad": "field"},
        )
        assert response.status_code == 422

    def test_get_detail(self, api_client: TestClient) -> None:
        """查询部门详情返回 200。"""

        dept = _create_dept(api_client)
        response = api_client.get(f"/api/v1/departments/{dept['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == "hq"
        assert body["childCount"] == 0

    def test_get_detail_not_found_404(
        self,
        api_client: TestClient,
    ) -> None:
        """查询不存在的部门返回 404。"""

        response = api_client.get(f"/api/v1/departments/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "ORG.DEPT_NOT_FOUND"

    def test_update(self, api_client: TestClient) -> None:
        """更新部门返回 200。"""

        dept = _create_dept(api_client)
        response = api_client.put(
            f"/api/v1/departments/{dept['id']}",
            json={"displayName": "新名称", "description": "描述"},
        )
        assert response.status_code == 200
        assert response.json()["displayName"] == "新名称"

    def test_enable_disable(self, api_client: TestClient) -> None:
        """启用和禁用部门。"""

        dept = _create_dept(api_client)
        # 禁用
        response = api_client.post(
            f"/api/v1/departments/{dept['id']}/disable",
        )
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"
        # 再次禁用 → 409
        response = api_client.post(
            f"/api/v1/departments/{dept['id']}/disable",
        )
        assert response.status_code == 409
        # 启用
        response = api_client.post(
            f"/api/v1/departments/{dept['id']}/enable",
        )
        assert response.status_code == 200
        assert response.json()["status"] == "active"

    def test_adjust_hierarchy(self, api_client: TestClient) -> None:
        """调整部门层级。"""

        parent = _create_dept(api_client, code="parent", display_name="P")
        child = _create_dept(api_client, code="child", display_name="C")
        response = api_client.put(
            f"/api/v1/departments/{child['id']}/hierarchy",
            json={"parentId": parent["id"], "sortOrder": 3},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["parentId"] == parent["id"]
        assert body["sortOrder"] == 3

    def test_adjust_hierarchy_direct_cycle_409(
        self,
        api_client: TestClient,
    ) -> None:
        """直接循环返回 409。"""

        dept = _create_dept(api_client, code="aa", display_name="A")
        response = api_client.put(
            f"/api/v1/departments/{dept['id']}/hierarchy",
            json={"parentId": dept["id"], "sortOrder": 0},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ORG.DEPT_CYCLE_DETECTED"

    def test_adjust_hierarchy_indirect_cycle_409(
        self,
        api_client: TestClient,
    ) -> None:
        """间接循环返回 409。"""

        a = _create_dept(api_client, code="aa", display_name="A")
        b = _create_dept(
            api_client,
            code="bb",
            display_name="B",
            parent_id=str(a["id"]),
        )
        c = _create_dept(
            api_client,
            code="cc",
            display_name="C",
            parent_id=str(b["id"]),
        )
        response = api_client.put(
            f"/api/v1/departments/{a['id']}/hierarchy",
            json={"parentId": str(c["id"]), "sortOrder": 0},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ORG.DEPT_CYCLE_DETECTED"

    def test_set_leader(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """设置部门负责人。"""

        user_id = asyncio.run(_seed_user(migrated_database_url, "orgleader"))
        dept = _create_dept(api_client)
        response = api_client.put(
            f"/api/v1/departments/{dept['id']}/leader",
            json={"leaderId": user_id},
        )
        assert response.status_code == 200
        assert response.json()["leaderId"] == user_id

    def test_delete_leaf(self, api_client: TestClient) -> None:
        """删除叶子部门返回 204。"""

        dept = _create_dept(api_client)
        response = api_client.delete(
            f"/api/v1/departments/{dept['id']}",
        )
        assert response.status_code == 204

    def test_delete_with_children_409(
        self,
        api_client: TestClient,
    ) -> None:
        """有子部门时删除返回 409。"""

        parent = _create_dept(api_client, code="pp", display_name="P")
        _create_dept(
            api_client,
            code="cc",
            display_name="C",
            parent_id=str(parent["id"]),
        )
        response = api_client.delete(
            f"/api/v1/departments/{parent['id']}",
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ORG.DEPT_HAS_CHILDREN"

    def test_get_tree(self, api_client: TestClient) -> None:
        """查询部门树返回正确的层级结构。"""

        root = _create_dept(api_client, code="root", display_name="Root")
        _create_dept(
            api_client,
            code="c1",
            display_name="C1",
            parent_id=str(root["id"]),
        )
        _create_dept(
            api_client,
            code="c2",
            display_name="C2",
            parent_id=str(root["id"]),
        )
        response = api_client.get("/api/v1/departments/tree")
        assert response.status_code == 200
        tree = response.json()
        assert len(tree) == 1
        assert tree[0]["code"] == "root"
        assert len(tree[0]["children"]) == 2

    def test_get_tree_exclude_disabled(
        self,
        api_client: TestClient,
    ) -> None:
        """includeDisabled=false 时禁用部门被排除。"""

        root = _create_dept(api_client, code="root", display_name="Root")
        _create_dept(
            api_client,
            code="active",
            display_name="Active",
            parent_id=str(root["id"]),
        )
        disabled_child = _create_dept(
            api_client,
            code="disabled",
            display_name="Disabled",
            parent_id=str(root["id"]),
        )
        # 禁用 disabled_child
        api_client.post(
            f"/api/v1/departments/{disabled_child['id']}/disable",
        )
        response = api_client.get(
            "/api/v1/departments/tree?includeDisabled=false",
        )
        assert response.status_code == 200
        tree = response.json()
        children = tree[0]["children"]
        assert len(children) == 1
        assert children[0]["code"] == "active"

    def test_disabled_visible_by_default(
        self,
        api_client: TestClient,
    ) -> None:
        """禁用部门默认在树查询中可见。"""

        root = _create_dept(api_client, code="root", display_name="Root")
        disabled_child = _create_dept(
            api_client,
            code="disabled",
            display_name="Disabled",
            parent_id=str(root["id"]),
        )
        api_client.post(
            f"/api/v1/departments/{disabled_child['id']}/disable",
        )
        response = api_client.get("/api/v1/departments/tree")
        assert response.status_code == 200
        tree = response.json()
        children = tree[0]["children"]
        assert len(children) == 1
        assert children[0]["status"] == "disabled"
