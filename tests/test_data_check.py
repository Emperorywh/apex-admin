"""数据完整性检查测试 — SPEC 25.3.

覆盖:
  - 循环检测纯函数（直接自身循环、间接循环、多分支循环、无循环）
  - data check 对健康数据库退出码 0
  - 注入菜单循环返回非 0 且报告具体位置
  - 注入部门循环返回非 0 且报告具体位置
  - 注入孤立角色权限关系返回非 0 且报告具体位置
  - 注入失效关联返回非 0 且报告具体位置

连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.data_check import (
    CheckIssue,
    DataCheckResult,
    detect_cycles,
    format_data_check_report,
    run_data_check,
)

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
    """清理全部业务表。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            # 按依赖顺序清理
            await conn.execute(text("DELETE FROM org_user_posts"))
            await conn.execute(text("DELETE FROM org_user_departments"))
            await conn.execute(text("DELETE FROM menu_role_menus"))
            await conn.execute(text("DELETE FROM rbac_user_roles"))
            await conn.execute(text("DELETE FROM rbac_role_permissions"))
            await conn.execute(text("DELETE FROM rbac_permissions"))
            await conn.execute(text("DELETE FROM rbac_roles"))
            await conn.execute(text("DELETE FROM menu_menus"))
            await conn.execute(text("DELETE FROM org_departments"))
            await conn.execute(text("DELETE FROM org_posts"))
            await conn.execute(text("DELETE FROM users"))
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


# ── 纯函数: 循环检测 ────────────────────────────────────────────────────────


class TestDataCheckCycles:
    """循环检测纯函数测试。"""

    @pytest.mark.g3
    @pytest.mark.unit
    def test_datacheck_no_cycle_healthy_tree(self) -> None:
        """健康树无循环。"""

        edges = {
            "a": None,
            "b": "a",
            "c": "a",
            "d": "b",
        }
        assert detect_cycles(edges) == []

    @pytest.mark.g3
    @pytest.mark.unit
    def test_datacheck_self_reference_cycle(self) -> None:
        """节点指向自身形成循环。"""

        edges = {
            "a": "a",
        }
        cycles = detect_cycles(edges)
        assert len(cycles) == 1
        assert cycles[0] == ("a",)

    @pytest.mark.g3
    @pytest.mark.unit
    def test_datacheck_indirect_cycle(self) -> None:
        """间接循环 a→b→c→a。"""

        edges = {
            "a": "b",
            "b": "c",
            "c": "a",
        }
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1
        # 循环应包含全部三个节点
        all_nodes = set()
        for cycle in cycles:
            all_nodes.update(cycle)
        assert all_nodes == {"a", "b", "c"}

    @pytest.mark.g3
    @pytest.mark.unit
    def test_datacheck_two_node_cycle(self) -> None:
        """双向循环 a→b→a。"""

        edges = {
            "a": "b",
            "b": "a",
        }
        cycles = detect_cycles(edges)
        assert len(cycles) >= 1
        all_nodes = set()
        for cycle in cycles:
            all_nodes.update(cycle)
        assert all_nodes == {"a", "b"}

    @pytest.mark.g3
    @pytest.mark.unit
    def test_datacheck_cycle_in_subtree(self) -> None:
        """子树中存在循环，其他部分正常。"""

        # 无循环的子树
        edges_no_cycle = {
            "root": None,
            "a": "root",
            "b": "a",
            "c": "b",
            "d": "c",
            "e": "d",
            "c2": "e",
        }
        assert detect_cycles(edges_no_cycle) == []

        # 实际循环: c→e→d→c（c 的父是 e，e 的父是 d，d 的父是 c）
        edges_real_cycle = {
            "root": None,
            "a": "root",
            "b": "a",
            "c": "e",  # c→e
            "d": "c",  # d→c
            "e": "d",  # e→d  形成循环 c→e→d→c
        }
        cycles = detect_cycles(edges_real_cycle)
        assert len(cycles) >= 1
        all_nodes = set()
        for cycle in cycles:
            all_nodes.update(cycle)
        assert all_nodes == {"c", "d", "e"}

    @pytest.mark.g3
    @pytest.mark.unit
    def test_datacheck_empty_edges(self) -> None:
        """空图无循环。"""

        assert detect_cycles({}) == []

    @pytest.mark.g3
    @pytest.mark.unit
    def test_datacheck_multiple_separate_cycles(self) -> None:
        """多个独立循环。"""

        edges = {
            "a": "b",
            "b": "a",
            "c": "d",
            "d": "c",
        }
        cycles = detect_cycles(edges)
        assert len(cycles) >= 2


# ── 集成测试: data check 数据库检查 ─────────────────────────────────────────


async def _insert_menu(
    engine: AsyncEngine,
    *,
    menu_id: str,
    parent_id: str | None,
    title: str = "test",
) -> None:
    """直接插入菜单记录。"""

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO menu_menus "
                "(id, parent_id, menu_type, title, name, path, component, "
                "icon, sort_order, visible, status, created_at, updated_at) "
                "VALUES "
                "(:id, :parent_id, 'page', :title, NULL, NULL, NULL, "
                "NULL, 0, TRUE, 'active', "
                "NOW() AT TIME ZONE 'UTC', "
                "NOW() AT TIME ZONE 'UTC')",
            ),
            {"id": menu_id, "parent_id": parent_id, "title": title},
        )


async def _insert_department(
    engine: AsyncEngine,
    *,
    dept_id: str,
    parent_id: str | None,
    code: str | None = None,
    leader_id: str | None = None,
) -> None:
    """直接插入部门记录。"""

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO org_departments "
                "(id, code, display_name, description, parent_id, status, "
                "sort_order, leader_id, created_at, updated_at) "
                "VALUES "
                "(:id, :code, :display_name, NULL, :parent_id, 'active', "
                "0, :leader_id, "
                "NOW() AT TIME ZONE 'UTC', "
                "NOW() AT TIME ZONE 'UTC')",
            ),
            {
                "id": dept_id,
                "code": code or f"dept-{dept_id[:8]}",
                "display_name": f"Dept-{dept_id[:8]}",
                "parent_id": parent_id,
                "leader_id": leader_id,
            },
        )


async def _insert_user(engine: AsyncEngine, user_id: str) -> None:
    """直接插入用户记录。"""

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users "
                "(id, username, display_name, password_hash, status, "
                "created_at, updated_at) "
                "VALUES "
                "(:id, :username, :display_name, 'dummy_hash', 'active', "
                "NOW() AT TIME ZONE 'UTC', "
                "NOW() AT TIME ZONE 'UTC')",
            ),
            {
                "id": user_id,
                "username": f"user-{user_id[:8]}",
                "display_name": f"User-{user_id[:8]}",
            },
        )


async def _insert_role(engine: AsyncEngine, role_id: str) -> None:
    """直接插入角色记录。"""

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rbac_roles "
                "(id, code, display_name, description, status, is_builtin, "
                "sort_order, created_at, updated_at) "
                "VALUES "
                "(:id, :code, :display_name, NULL, 'active', FALSE, "
                "0, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')",
            ),
            {
                "id": role_id,
                "code": f"role-{role_id[:8]}",
                "display_name": f"Role-{role_id[:8]}",
            },
        )


async def _run_check(engine: AsyncEngine) -> DataCheckResult:
    """在给定引擎上执行 data check。"""

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    uow = SqlAlchemyUnitOfWork(engine)
    async with uow:
        return await run_data_check(uow.session)


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_healthy_database_passes(migrated_database_url: str) -> None:
    """健康数据库退出码 0 — 无任何完整性问题。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    async def _run() -> DataCheckResult:
        try:
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result.healthy
    assert result.issues == []


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_menu_cycle_detected(migrated_database_url: str) -> None:
    """注入菜单循环返回非 0 且报告具体位置。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    menu_a = str(uuid4())
    menu_b = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            # 先插入（无循环），再 UPDATE 制造循环
            await _insert_menu(engine, menu_id=menu_a, parent_id=None, title="A")
            await _insert_menu(engine, menu_id=menu_b, parent_id=menu_a, title="B")
            # UPDATE a 的 parent 指向 b，形成 a→b→a 循环
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE menu_menus SET parent_id = :pid WHERE id = :id",
                    ),
                    {"pid": menu_b, "id": menu_a},
                )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert not result.healthy
    menu_cycle_issues = [i for i in result.issues if i.check == "menu_cycle"]
    assert len(menu_cycle_issues) >= 1
    assert "menu_menus" in menu_cycle_issues[0].location


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_dept_cycle_detected(migrated_database_url: str) -> None:
    """注入部门循环返回非 0 且报告具体位置。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    dept_a = str(uuid4())
    dept_b = str(uuid4())
    dept_c = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            # 先插入链式结构 c→b→a→None，再 UPDATE 制造循环
            await _insert_department(engine, dept_id=dept_c, parent_id=None, code="C")
            await _insert_department(engine, dept_id=dept_b, parent_id=dept_c, code="B")
            await _insert_department(engine, dept_id=dept_a, parent_id=dept_b, code="A")
            # UPDATE c 的 parent 指向 a，形成 c→a→b→c 循环
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE org_departments SET parent_id = :pid WHERE id = :id",
                    ),
                    {"pid": dept_a, "id": dept_c},
                )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert not result.healthy
    dept_cycle_issues = [i for i in result.issues if i.check == "dept_cycle"]
    assert len(dept_cycle_issues) >= 1
    assert "org_departments" in dept_cycle_issues[0].location


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_orphaned_user_role_detected(migrated_database_url: str) -> None:
    """注入孤立用户角色关系返回非 0 且报告具体位置。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    fake_user_id = str(uuid4())
    role_id = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            await _insert_role(engine, role_id)
            # 插入引用不存在用户的角色关系
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO rbac_user_roles "
                        "(user_id, role_id, created_at) "
                        "VALUES "
                        "(:user_id, :role_id, "
                        "NOW() AT TIME ZONE 'UTC')",
                    ),
                    {"user_id": fake_user_id, "role_id": role_id},
                )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert not result.healthy
    orphan_issues = [i for i in result.issues if i.check == "orphaned_user_role"]
    assert len(orphan_issues) >= 1
    assert fake_user_id in orphan_issues[0].location


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_orphaned_role_menu_detected(migrated_database_url: str) -> None:
    """注入失效角色菜单关系返回非 0 且报告具体位置。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    fake_role_id = str(uuid4())
    menu_id = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            await _insert_menu(engine, menu_id=menu_id, parent_id=None)
            # 插入引用不存在角色的菜单关联
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO menu_role_menus "
                        "(role_id, menu_id, created_at) "
                        "VALUES "
                        "(:role_id, :menu_id, "
                        "NOW() AT TIME ZONE 'UTC')",
                    ),
                    {"role_id": fake_role_id, "menu_id": menu_id},
                )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert not result.healthy
    orphan_issues = [i for i in result.issues if i.check == "orphaned_role_menu"]
    assert len(orphan_issues) >= 1
    assert fake_role_id in orphan_issues[0].location


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_invalid_dept_leader_detected(migrated_database_url: str) -> None:
    """注入失效部门负责人返回非 0 且报告具体位置。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    fake_leader_id = str(uuid4())
    dept_id = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            await _insert_department(
                engine,
                dept_id=dept_id,
                parent_id=None,
                code="leader_test",
                leader_id=fake_leader_id,
            )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert not result.healthy
    leader_issues = [i for i in result.issues if i.check == "invalid_dept_leader"]
    assert len(leader_issues) >= 1
    assert fake_leader_id in leader_issues[0].location


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_orphaned_user_dept_detected(
    migrated_database_url: str,
) -> None:
    """注入孤立用户部门关系返回非 0。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    fake_user_id = str(uuid4())
    dept_id = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            await _insert_department(engine, dept_id=dept_id, parent_id=None, code="UD")
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO org_user_departments "
                        "(id, user_id, department_id, is_primary, created_at) "
                        "VALUES "
                        "(:id, :user_id, :dept_id, TRUE, "
                        "NOW() AT TIME ZONE 'UTC')",
                    ),
                    {
                        "id": str(uuid4()),
                        "user_id": fake_user_id,
                        "dept_id": dept_id,
                    },
                )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert not result.healthy
    issues = [i for i in result.issues if i.check == "orphaned_user_department"]
    assert len(issues) >= 1


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_orphaned_user_post_detected(migrated_database_url: str) -> None:
    """注入孤立用户岗位关系返回非 0。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    fake_user_id = str(uuid4())
    post_id = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO org_posts "
                        "(id, code, display_name, description, status, "
                        "sort_order, created_at, updated_at) "
                        "VALUES "
                        "(:id, :code, :display_name, NULL, 'active', "
                        "0, NOW() AT TIME ZONE 'UTC', "
                        "NOW() AT TIME ZONE 'UTC')",
                    ),
                    {
                        "id": post_id,
                        "code": f"post-{post_id[:8]}",
                        "display_name": "Post",
                    },
                )
                await conn.execute(
                    text(
                        "INSERT INTO org_user_posts "
                        "(id, user_id, post_id, created_at) "
                        "VALUES "
                        "(:id, :user_id, :post_id, "
                        "NOW() AT TIME ZONE 'UTC')",
                    ),
                    {
                        "id": str(uuid4()),
                        "user_id": fake_user_id,
                        "post_id": post_id,
                    },
                )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert not result.healthy
    issues = [i for i in result.issues if i.check == "orphaned_user_post"]
    assert len(issues) >= 1


@pytest.mark.g3
@pytest.mark.integration
def test_datacheck_healthy_with_valid_data(migrated_database_url: str) -> None:
    """有正常数据但无问题的数据库也退出码 0。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(migrated_database_url)

    user_id = str(uuid4())
    role_id = str(uuid4())
    dept_id = str(uuid4())
    menu_id = str(uuid4())

    async def _setup_and_check() -> DataCheckResult:
        try:
            await _insert_user(engine, user_id)
            await _insert_role(engine, role_id)
            await _insert_department(engine, dept_id=dept_id, parent_id=None, code="OK")
            await _insert_menu(engine, menu_id=menu_id, parent_id=None)
            # 正常关联
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO rbac_user_roles "
                        "(user_id, role_id, created_at) "
                        "VALUES (:uid, :rid, "
                        "NOW() AT TIME ZONE 'UTC')",
                    ),
                    {"uid": user_id, "rid": role_id},
                )
            return await _run_check(engine)
        finally:
            await engine.dispose()

    result = asyncio.run(_setup_and_check())
    assert result.healthy
    assert result.issues == []


# ── 报告格式化测试 ──────────────────────────────────────────────────────────


@pytest.mark.g3
@pytest.mark.unit
def test_datacheck_format_report_healthy() -> None:
    """健康结果报告格式正确。"""

    result = DataCheckResult(issues=[])
    report = format_data_check_report(result)
    assert "通过" in report
    assert "未发现" in report


@pytest.mark.g3
@pytest.mark.unit
def test_datacheck_format_report_with_issues() -> None:
    """有问题结果报告格式正确。"""

    result = DataCheckResult(
        issues=[
            CheckIssue(
                check="menu_cycle",
                location="menu_menus: cycle a -> b -> a",
                detail="菜单层级存在循环",
            ),
        ],
    )
    report = format_data_check_report(result)
    assert "未通过" in report
    assert "1" in report
    assert "menu_cycle" in report
    assert "菜单层级存在循环" in report
