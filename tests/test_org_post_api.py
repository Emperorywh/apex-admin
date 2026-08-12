"""岗位与用户组织关系 API 契约测试 — SPEC 14.2 / 14.3 / 11.1 / 28.4.

覆盖 TASK-020 验收标准:
  - AC-0: 岗位 API 契约全部通过（创建/查询/更新/启用禁用/
          为用户分配/移除用户岗位；分配幂等且防重复）
  - AC-2: 用户详情经跨模块公开 Port 聚合返回部门与岗位关系，
          无跨模块 ORM 访问（集成测试证明）

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
    """清理全部业务表。"""

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


async def _seed_user(database_url: str, username: str = "apiuser") -> str:
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
                    "u": f"{username}_{user_id.hex[:8]}",
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
    request_id="test-post-req",
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
    """创建带超管权限的 TestClient。"""

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


def _create_post(
    client: TestClient,
    *,
    code: str = "dev",
    display_name: str = "开发",
) -> dict[str, object]:
    """通过 API 创建岗位并返回响应体。"""

    response = client.post(
        "/api/v1/posts",
        json={"code": code, "displayName": display_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_dept(
    client: TestClient,
    *,
    code: str = "hq",
    display_name: str = "总部",
) -> dict[str, object]:
    """通过 API 创建部门并返回响应体。"""

    response = client.post(
        "/api/v1/departments",
        json={"code": code, "displayName": display_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 岗位 CRUD API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestPostCRUDAPI:
    """岗位 CRUD API 契约测试 — SPEC 14.2 / 9.3."""

    def test_create_returns_201_with_location(
        self,
        api_client: TestClient,
    ) -> None:
        """创建岗位返回 201 和 Location 头（SPEC 9.3）。"""

        response = api_client.post(
            "/api/v1/posts",
            json={"code": "engineer", "displayName": "工程师"},
        )
        assert response.status_code == 201
        assert "location" in {k.lower() for k in response.headers}
        body = response.json()
        assert body["code"] == "engineer"
        assert body["status"] == "active"

    def test_create_duplicate_code_409(
        self,
        api_client: TestClient,
    ) -> None:
        """重复编码返回 409。"""

        _create_post(api_client, code="dup", display_name="D1")
        response = api_client.post(
            "/api/v1/posts",
            json={"code": "dup", "displayName": "D2"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ORG.POST_ALREADY_EXISTS"

    def test_create_unknown_field_422(
        self,
        api_client: TestClient,
    ) -> None:
        """未知字段返回 422。"""

        response = api_client.post(
            "/api/v1/posts",
            json={"code": "test", "displayName": "T", "bad": "field"},
        )
        assert response.status_code == 422

    def test_list_posts(self, api_client: TestClient) -> None:
        """查询岗位列表返回 200。"""

        _create_post(api_client, code="p1", display_name="岗位1")
        _create_post(api_client, code="p2", display_name="岗位2")
        response = api_client.get("/api/v1/posts")
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_get_detail(self, api_client: TestClient) -> None:
        """查询岗位详情返回 200。"""

        post = _create_post(api_client)
        response = api_client.get(f"/api/v1/posts/{post['id']}")
        assert response.status_code == 200
        assert response.json()["code"] == "dev"

    def test_get_detail_not_found_404(
        self,
        api_client: TestClient,
    ) -> None:
        """查询不存在的岗位返回 404。"""

        response = api_client.get(f"/api/v1/posts/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "ORG.POST_NOT_FOUND"

    def test_update(self, api_client: TestClient) -> None:
        """更新岗位返回 200。"""

        post = _create_post(api_client)
        response = api_client.put(
            f"/api/v1/posts/{post['id']}",
            json={"displayName": "新名称", "description": "描述"},
        )
        assert response.status_code == 200
        assert response.json()["displayName"] == "新名称"

    def test_enable_disable(self, api_client: TestClient) -> None:
        """启用禁用岗位返回正确状态。"""

        post = _create_post(api_client)
        # 禁用
        r = api_client.post(f"/api/v1/posts/{post['id']}/disable")
        assert r.status_code == 200
        assert r.json()["status"] == "disabled"
        # 再次禁用返回 409
        r2 = api_client.post(f"/api/v1/posts/{post['id']}/disable")
        assert r2.status_code == 409
        # 启用
        r3 = api_client.post(f"/api/v1/posts/{post['id']}/enable")
        assert r3.status_code == 200
        assert r3.json()["status"] == "active"
        # 再次启用返回 409
        r4 = api_client.post(f"/api/v1/posts/{post['id']}/enable")
        assert r4.status_code == 409

    def test_delete(self, api_client: TestClient) -> None:
        """删除无关联用户的岗位返回 204。"""

        post = _create_post(api_client)
        response = api_client.delete(f"/api/v1/posts/{post['id']}")
        assert response.status_code == 204


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0 (续): 用户岗位分配/移除 API 契约 — 幂等且防重复
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestUserPostAssignmentAPI:
    """用户岗位分配与移除 API 契约测试 — SPEC 14.2."""

    def test_assign_user_post(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """为用户分配岗位返回 200。"""

        user_id = asyncio.run(_seed_user(migrated_database_url))
        post = _create_post(api_client)
        response = api_client.post(
            f"/api/v1/users/{user_id}/posts",
            json={"postId": post["id"]},
        )
        assert response.status_code == 200

    def test_assign_user_post_idempotent(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """重复分配同一岗位幂等返回 200。"""

        user_id = asyncio.run(_seed_user(migrated_database_url))
        post = _create_post(api_client)
        r1 = api_client.post(
            f"/api/v1/users/{user_id}/posts",
            json={"postId": post["id"]},
        )
        assert r1.status_code == 200
        r2 = api_client.post(
            f"/api/v1/users/{user_id}/posts",
            json={"postId": post["id"]},
        )
        assert r2.status_code == 200

    def test_assign_disabled_post_409(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """为用户分配已禁用岗位返回 409。"""

        user_id = asyncio.run(_seed_user(migrated_database_url))
        post = _create_post(api_client)
        api_client.post(f"/api/v1/posts/{post['id']}/disable")
        response = api_client.post(
            f"/api/v1/users/{user_id}/posts",
            json={"postId": post["id"]},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ORG.POST_DISABLED"

    def test_remove_user_post(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """移除用户岗位返回 204。"""

        user_id = asyncio.run(_seed_user(migrated_database_url))
        post = _create_post(api_client)
        api_client.post(
            f"/api/v1/users/{user_id}/posts",
            json={"postId": post["id"]},
        )
        response = api_client.delete(
            f"/api/v1/users/{user_id}/posts/{post['id']}",
        )
        assert response.status_code == 204

    def test_remove_nonexistent_post_409(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """移除不存在的用户岗位关系返回 409。"""

        user_id = asyncio.run(_seed_user(migrated_database_url))
        post = _create_post(api_client)
        response = api_client.delete(
            f"/api/v1/users/{user_id}/posts/{post['id']}",
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ORG.USER_POST_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 用户详情跨模块聚合返回部门与岗位关系
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestUserDetailCrossModuleAggregation:
    """用户详情跨模块聚合测试 — SPEC 11.1 / 14.3 / 5.5."""

    def test_user_detail_includes_dept_and_posts(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """用户详情聚合返回部门与岗位关系。

        SPEC 11.1: "通过 G3 后同时返回部门和岗位关系"。
        通过组合根注入 org 的公开 Port（UserOrgPort）聚合，
        identity 不直接访问 org 的 ORM 模型（SPEC 5.5）。
        """

        user_id = asyncio.run(_seed_user(migrated_database_url, "agguser"))
        dept = _create_dept(api_client, code="eng", display_name="工程部")
        post = _create_post(api_client, code="dev", display_name="开发")

        # 设置主部门
        r_dept = api_client.put(
            f"/api/v1/users/{user_id}/department",
            json={"departmentId": dept["id"]},
        )
        assert r_dept.status_code == 200

        # 分配岗位
        r_post = api_client.post(
            f"/api/v1/users/{user_id}/posts",
            json={"postId": post["id"]},
        )
        assert r_post.status_code == 200

        # 查询用户详情 — 应包含部门和岗位
        response = api_client.get(f"/api/v1/users/{user_id}")
        assert response.status_code == 200
        body = response.json()

        # department 字段存在且正确
        assert body["department"] is not None
        assert body["department"]["departmentCode"] == "eng"
        assert body["department"]["isPrimary"] is True

        # posts 字段存在且包含分配的岗位
        assert len(body["posts"]) == 1
        assert body["posts"][0]["postCode"] == "dev"

    def test_user_detail_without_org_relations(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """无组织关系的用户详情返回 null 部门和空岗位列表。"""

        user_id = asyncio.run(_seed_user(migrated_database_url, "noorg"))
        response = api_client.get(f"/api/v1/users/{user_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["department"] is None
        assert body["posts"] == []
