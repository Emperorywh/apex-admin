"""菜单模块集成测试 — SPEC 15.1 / 15.2.

覆盖:
  - 菜单 CRUD 与树查询。
  - 启用禁用。
  - 循环防护（直接/间接循环）。
  - 角色菜单分配（全量替换、幂等）与移除（幂等）。
  - 当前用户菜单树按启用角色聚合。
  - 菜单变更提交后下一次查询立即读取新关系（无缓存）。
  - 当前用户权限编码端点返回启用角色权限并集。

连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.context import UseCaseContext
from app.application.ports import SystemClock, UuidGenerator
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.audit.adapter import SqlAlchemyAuditRepository
from app.modules.menu.errors import (
    MenuAlreadyActiveError,
    MenuAlreadyDisabledError,
    MenuCycleError,
    MenuHasChildrenError,
    MenuNotFoundError,
)
from app.modules.menu.schemas import (
    AssignRoleMenusRequest,
    MenuCreateRequest,
    MenuHierarchyRequest,
    MenuUpdateRequest,
)
from app.modules.menu.use_case import MenuUseCase
from app.modules.rbac.adapter import SqlAlchemyUserRbacAdapter

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine


# ── 迁移与清理 ─────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from alembic import command

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_tables(database_url: str) -> None:
    """清理菜单与关联表。"""

    from app.infrastructure.db.engine import create_db_engine

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

    from app.infrastructure.db.engine import create_db_engine

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

    from app.infrastructure.db.engine import create_db_engine

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

    from app.infrastructure.db.engine import create_db_engine

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

    from app.infrastructure.db.engine import create_db_engine

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

    from app.infrastructure.db.engine import create_db_engine

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

_TEST_ACTOR_ID = str(uuid4())


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


def _make_use_case(engine: AsyncEngine) -> MenuUseCase:
    """构造 MenuUseCase。"""

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    return MenuUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=lambda session: SqlAlchemyAuditRepository(session),
        user_rbac_port_factory=lambda session: SqlAlchemyUserRbacAdapter(session),
    )


def _ctx() -> UseCaseContext:
    """构造测试上下文。"""

    return UseCaseContext(
        request_id="test-menu-req",
        actor_id=_TEST_ACTOR_ID,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 菜单 CRUD 与树查询
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestMenuCRUDIntegration:
    """菜单 CRUD 集成测试 — SPEC 15.1."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> MenuUseCase:
        """构造 UseCase 与 engine。"""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_create_menu(self, use_case: MenuUseCase) -> None:
        """创建菜单返回正确数据。"""

        ctx = _ctx()
        request = MenuCreateRequest(
            menu_type="directory",
            title="系统管理",
            name="system",
            icon="setting",
            sort_order=0,
            visible=True,
        )
        result = asyncio.run(use_case.create_menu(ctx, request))
        assert result["title"] == "系统管理"
        assert result["menu_type"] == "directory"
        assert result["status"] == "active"
        assert result["visible"] is True
        assert result["icon"] == "setting"

    def test_create_menu_with_all_types(self, use_case: MenuUseCase) -> None:
        """目录/页面/外链三种类型均可创建 — SPEC 15.1."""

        ctx = _ctx()
        for mtype in ("directory", "page", "link"):
            request = MenuCreateRequest(
                menu_type=mtype,
                title=f"Test {mtype}",
                name=f"test_{mtype}",
                path=f"/test/{mtype}",
                component=f"Test{mtype.title()}" if mtype == "page" else None,
            )
            result = asyncio.run(use_case.create_menu(ctx, request))
            assert result["menu_type"] == mtype

    def test_get_detail(self, use_case: MenuUseCase) -> None:
        """查询菜单详情。"""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="Dashboard"),
            ),
        )
        detail = asyncio.run(
            use_case.get_menu_detail(ctx, created["id"]),
        )
        assert detail["title"] == "Dashboard"

    def test_get_detail_not_found(self, use_case: MenuUseCase) -> None:
        """查询不存在的菜单返回 404。"""

        ctx = _ctx()
        with pytest.raises(MenuNotFoundError):
            asyncio.run(use_case.get_menu_detail(ctx, uuid4()))

    def test_update_menu(self, use_case: MenuUseCase) -> None:
        """更新菜单信息。"""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="Old Title"),
            ),
        )
        result = asyncio.run(
            use_case.update_menu(
                ctx,
                created["id"],
                MenuUpdateRequest(title="New Title", visible=False),
            ),
        )
        assert result["title"] == "New Title"
        assert result["visible"] is False

    def test_enable_disable(self, use_case: MenuUseCase) -> None:
        """启用和禁用菜单。"""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="directory", title="Test"),
            ),
        )
        # 禁用
        disabled = asyncio.run(use_case.disable_menu(ctx, created["id"]))
        assert disabled["status"] == "disabled"
        # 再次禁用 → 抛异常
        with pytest.raises(MenuAlreadyDisabledError):
            asyncio.run(use_case.disable_menu(ctx, created["id"]))
        # 启用
        enabled = asyncio.run(use_case.enable_menu(ctx, created["id"]))
        assert enabled["status"] == "active"
        # 再次启用 → 抛异常
        with pytest.raises(MenuAlreadyActiveError):
            asyncio.run(use_case.enable_menu(ctx, created["id"]))

    def test_get_tree(self, use_case: MenuUseCase) -> None:
        """查询菜单树返回正确的层级结构。"""

        ctx = _ctx()
        root = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="directory", title="Root"),
            ),
        )
        asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(
                    menu_type="page",
                    title="Child",
                    parent_id=root["id"],
                ),
            ),
        )
        tree = asyncio.run(use_case.get_menu_tree(ctx))
        assert len(tree) == 1
        assert tree[0]["title"] == "Root"
        assert len(tree[0]["children"]) == 1

    def test_delete_menu(self, use_case: MenuUseCase) -> None:
        """删除叶子菜单。"""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="ToDelete"),
            ),
        )
        asyncio.run(use_case.delete_menu(ctx, created["id"]))
        with pytest.raises(MenuNotFoundError):
            asyncio.run(use_case.get_menu_detail(ctx, created["id"]))

    def test_delete_with_children_rejected(self, use_case: MenuUseCase) -> None:
        """有子菜单时删除被拒绝 — SPEC 15.1."""

        ctx = _ctx()
        root = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="directory", title="Root"),
            ),
        )
        asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(
                    menu_type="page",
                    title="Child",
                    parent_id=root["id"],
                ),
            ),
        )
        with pytest.raises(MenuHasChildrenError):
            asyncio.run(use_case.delete_menu(ctx, root["id"]))


# ═══════════════════════════════════════════════════════════════════════════════
# 循环防护
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestMenuCycleProtectionIntegration:
    """菜单循环防护集成测试 — SPEC 15.1."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> MenuUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_direct_cycle_rejected(self, use_case: MenuUseCase) -> None:
        """直接循环（自身为父菜单）被拒绝。"""

        ctx = _ctx()
        menu = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="directory", title="A"),
            ),
        )
        with pytest.raises(MenuCycleError):
            asyncio.run(
                use_case.adjust_hierarchy(
                    ctx,
                    menu["id"],
                    MenuHierarchyRequest(parent_id=menu["id"], sort_order=0),
                ),
            )

    def test_indirect_cycle_rejected(self, use_case: MenuUseCase) -> None:
        """间接循环（后代为父菜单）被拒绝。"""

        ctx = _ctx()
        a = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="directory", title="A"),
            ),
        )
        b = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(
                    menu_type="directory",
                    title="B",
                    parent_id=a["id"],
                ),
            ),
        )
        c = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(
                    menu_type="directory",
                    title="C",
                    parent_id=b["id"],
                ),
            ),
        )
        # A → C 的子菜单 = 循环
        with pytest.raises(MenuCycleError):
            asyncio.run(
                use_case.adjust_hierarchy(
                    ctx,
                    a["id"],
                    MenuHierarchyRequest(parent_id=c["id"], sort_order=0),
                ),
            )

    def test_valid_hierarchy_adjust(self, use_case: MenuUseCase) -> None:
        """有效的层级调整成功。"""

        ctx = _ctx()
        parent = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="directory", title="Parent"),
            ),
        )
        child = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="Child"),
            ),
        )
        result = asyncio.run(
            use_case.adjust_hierarchy(
                ctx,
                child["id"],
                MenuHierarchyRequest(parent_id=parent["id"], sort_order=3),
            ),
        )
        assert result["parent_id"] == parent["id"]
        assert result["sort_order"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 角色菜单分配
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestRoleMenuAssignmentIntegration:
    """角色菜单分配集成测试 — SPEC 15.1."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> MenuUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_assign_menus_idempotent(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """角色菜单分配全量替换幂等 — SPEC 15.1."""

        ctx = _ctx()
        role_id = uuid4()
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "editor"))

        menu1 = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="M1"),
            ),
        )
        menu2 = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="M2"),
            ),
        )

        # 第一次分配
        result1 = asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(menu_ids=[menu1["id"], menu2["id"]]),
            ),
        )
        assert len(result1["menu_ids"]) == 2

        # 第二次分配相同内容 → 幂等
        result2 = asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(menu_ids=[menu1["id"], menu2["id"]]),
            ),
        )
        assert len(result2["menu_ids"]) == 2

        # 验证没有重复记录
        ids = asyncio.run(use_case.get_role_menu_ids(ctx, role_id))
        assert len(ids) == 2

    def test_assign_menus_replaces(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """全量替换移除旧关联。"""

        ctx = _ctx()
        role_id = uuid4()
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "admin"))

        menu1 = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="M1"),
            ),
        )
        menu2 = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="M2"),
            ),
        )

        # 先分配 menu1
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(menu_ids=[menu1["id"]]),
            ),
        )
        # 替换为 menu2
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(menu_ids=[menu2["id"]]),
            ),
        )
        ids = asyncio.run(use_case.get_role_menu_ids(ctx, role_id))
        assert menu2["id"] in ids
        assert menu1["id"] not in ids

    def test_remove_role_menu_idempotent(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """移除角色菜单幂等 — SPEC 15.1."""

        ctx = _ctx()
        role_id = uuid4()
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "viewer"))

        menu = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="M"),
            ),
        )
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(menu_ids=[menu["id"]]),
            ),
        )

        # 第一次移除 → 成功
        asyncio.run(use_case.remove_role_menu(ctx, role_id, menu["id"]))
        # 第二次移除 → 幂等（无异常）
        asyncio.run(use_case.remove_role_menu(ctx, role_id, menu["id"]))

        ids = asyncio.run(use_case.get_role_menu_ids(ctx, role_id))
        assert len(ids) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 当前用户菜单树与权限
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestCurrentUserMenuIntegration:
    """当前用户菜单与权限集成测试 — SPEC 15.2."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> MenuUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_current_user_menu_tree(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """当前用户菜单树按启用角色聚合 — SPEC 15.2."""

        user_id = uuid4()
        role_id = uuid4()
        role2_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "r1"))
        asyncio.run(_seed_role(migrated_database_url, str(role2_id), "r2"))
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role_id))
        )
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role2_id)),
        )

        ctx = UseCaseContext(request_id="test", actor_id=str(user_id))

        # 创建菜单
        root = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="directory", title="Root"),
            ),
        )
        child = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(
                    menu_type="page",
                    title="Child",
                    parent_id=root["id"],
                ),
            ),
        )

        # 分配 root 给 role1，child 给 role2
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(menu_ids=[root["id"]]),
            ),
        )
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role2_id,
                AssignRoleMenusRequest(menu_ids=[child["id"]]),
            ),
        )

        # 查询当前用户菜单树 → 应包含 root 和 child（来自两个角色）
        tree = asyncio.run(use_case.get_current_user_menu_tree(ctx))
        assert len(tree) >= 1
        all_titles = _collect_titles(tree)
        assert "Root" in all_titles
        assert "Child" in all_titles

    def test_disabled_role_excluded(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """禁用角色的菜单不出现在当前用户菜单树 — SPEC 15.2."""

        user_id = uuid4()
        active_role_id = uuid4()
        disabled_role_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(active_role_id), "active_r"))
        asyncio.run(
            _seed_role(
                migrated_database_url,
                str(disabled_role_id),
                "disabled_r",
                status="disabled",
            ),
        )
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(active_role_id)),
        )
        asyncio.run(
            _assign_user_role(
                migrated_database_url,
                str(user_id),
                str(disabled_role_id),
            ),
        )

        ctx = UseCaseContext(request_id="test", actor_id=str(user_id))

        active_menu = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="Active Menu"),
            ),
        )
        disabled_menu = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="Disabled Menu"),
            ),
        )

        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                active_role_id,
                AssignRoleMenusRequest(menu_ids=[active_menu["id"]]),
            ),
        )
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                disabled_role_id,
                AssignRoleMenusRequest(menu_ids=[disabled_menu["id"]]),
            ),
        )

        tree = asyncio.run(use_case.get_current_user_menu_tree(ctx))
        titles = _collect_titles(tree)
        assert "Active Menu" in titles
        assert "Disabled Menu" not in titles

    def test_menu_change_immediate_effect(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """菜单变更提交后下一次查询立即读取新关系 — SPEC 15.2."""

        user_id = uuid4()
        role_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "editor"))
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role_id))
        )

        ctx = UseCaseContext(request_id="test", actor_id=str(user_id))

        # 初始菜单树为空
        tree = asyncio.run(use_case.get_current_user_menu_tree(ctx))
        assert tree == []

        # 创建菜单并分配
        menu = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="New Menu"),
            ),
        )
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(menu_ids=[menu["id"]]),
            ),
        )

        # 下一次查询立即返回新关系
        tree = asyncio.run(use_case.get_current_user_menu_tree(ctx))
        titles = _collect_titles(tree)
        assert "New Menu" in titles

    def test_current_user_permissions(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """当前用户权限编码端点返回启用角色权限并集 — SPEC 15.2."""

        user_id = uuid4()
        role1_id = uuid4()
        role2_id = uuid4()
        perm1_id = uuid4()
        perm2_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(role1_id), "r1"))
        asyncio.run(_seed_role(migrated_database_url, str(role2_id), "r2"))
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role1_id)),
        )
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role2_id)),
        )
        asyncio.run(
            _seed_permission(migrated_database_url, str(perm1_id), "system:user:read"),
        )
        asyncio.run(
            _seed_permission(migrated_database_url, str(perm2_id), "system:user:write"),
        )
        asyncio.run(
            _assign_role_permission(
                migrated_database_url, str(role1_id), str(perm1_id)
            ),
        )
        asyncio.run(
            _assign_role_permission(
                migrated_database_url, str(role2_id), str(perm2_id)
            ),
        )

        ctx = UseCaseContext(request_id="test", actor_id=str(user_id))
        perms = asyncio.run(use_case.get_current_user_permissions(ctx))
        assert "system:user:read" in perms
        assert "system:user:write" in perms

    def test_hidden_menu_excluded_from_user_tree(
        self,
        use_case: MenuUseCase,
        migrated_database_url: str,
    ) -> None:
        """不可见菜单不出现在当前用户菜单树（SPEC 23.5）。"""

        user_id = uuid4()
        role_id = uuid4()
        asyncio.run(_seed_user(migrated_database_url, str(user_id)))
        asyncio.run(_seed_role(migrated_database_url, str(role_id), "editor"))
        asyncio.run(
            _assign_user_role(migrated_database_url, str(user_id), str(role_id)),
        )

        ctx = UseCaseContext(request_id="test", actor_id=str(user_id))

        visible_menu = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="Visible", visible=True),
            ),
        )
        hidden_menu = asyncio.run(
            use_case.create_menu(
                ctx,
                MenuCreateRequest(menu_type="page", title="Hidden", visible=False),
            ),
        )
        asyncio.run(
            use_case.assign_role_menus(
                ctx,
                role_id,
                AssignRoleMenusRequest(
                    menu_ids=[visible_menu["id"], hidden_menu["id"]],
                ),
            ),
        )

        tree = asyncio.run(use_case.get_current_user_menu_tree(ctx))
        titles = _collect_titles(tree)
        assert "Visible" in titles
        assert "Hidden" not in titles


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
