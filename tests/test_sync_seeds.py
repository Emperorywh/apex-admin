"""种子同步命令测试 — SPEC 25.3 / 8.5.

覆盖:
  - admin sync-seeds 连续两次执行结果一致且不产生重复编码（幂等）
  - 菜单种子初始化器幂等 upsert
  - 字典种子初始化器幂等 upsert
  - 全部种子初始化器收集与执行

连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import text

from app.core.initialization.framework import InitializationRunner
from app.modules.dict.initializers import DictSeedInitializer
from app.modules.menu.initializers import MenuSeedInitializer

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
    """清理种子相关表。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM dict_items"))
            await conn.execute(text("DELETE FROM dict_types"))
            await conn.execute(text("DELETE FROM menu_role_menus"))
            await conn.execute(text("DELETE FROM menu_menus"))
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
    """每个测试前后清理种子相关表。"""

    asyncio.run(_cleanup_tables(migrated_database_url))
    yield
    asyncio.run(_cleanup_tables(migrated_database_url))


# ── 辅助函数 ──────────────────────────────────────────────────────────────


def _expected_menu_id(code: str) -> str:
    """计算种子菜单的确定性 UUID."""

    return str(uuid5(NAMESPACE_URL, f"apex:menu:{code}"))


async def _count_menus(engine: AsyncEngine) -> int:
    """统计菜单数量。"""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM menu_menus"))
        return int(result.scalar() or 0)


async def _count_dict_types(engine: AsyncEngine) -> int:
    """统计字典类型数量。"""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM dict_types"))
        return int(result.scalar() or 0)


async def _count_dict_items(engine: AsyncEngine) -> int:
    """统计字典项数量。"""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM dict_items"))
        return int(result.scalar() or 0)


async def _get_menu_titles(engine: AsyncEngine) -> set[str]:
    """获取全部菜单标题集合。"""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT title FROM menu_menus"))
        return {row[0] for row in result.fetchall()}


async def _get_dict_type_codes(engine: AsyncEngine) -> set[str]:
    """获取全部字典类型编码集合。"""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT code FROM dict_types"))
        return {row[0] for row in result.fetchall()}


# ── 测试: 菜单种子初始化器 ─────────────────────────────────────────────────


@pytest.mark.g3
@pytest.mark.integration
def test_menu_seed_creates_menus(migrated_database_url: str) -> None:
    """菜单种子初始化器创建基础菜单。"""

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(migrated_database_url)

    async def _run() -> tuple[int, set[str]]:
        try:
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                init = MenuSeedInitializer()
                await init.initialize(uow.session)
                await uow.commit()

            count = await _count_menus(engine)
            titles = await _get_menu_titles(engine)
            return count, titles
        finally:
            await engine.dispose()

    count, titles = asyncio.run(_run())
    assert count > 0
    assert "系统管理" in titles
    assert "用户管理" in titles
    assert "角色管理" in titles


@pytest.mark.g3
@pytest.mark.integration
def test_menu_seed_idempotent(migrated_database_url: str) -> None:
    """菜单种子初始化器连续两次执行不产生重复。"""

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(migrated_database_url)

    async def _run() -> tuple[int, int]:
        try:
            # 第一次执行
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                init = MenuSeedInitializer()
                await init.initialize(uow.session)
                await uow.commit()

            count1 = await _count_menus(engine)

            # 第二次执行
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                init = MenuSeedInitializer()
                await init.initialize(uow.session)
                await uow.commit()

            count2 = await _count_menus(engine)
            return count1, count2
        finally:
            await engine.dispose()

    count1, count2 = asyncio.run(_run())
    assert count1 == count2
    assert count1 > 0


@pytest.mark.g3
@pytest.mark.integration
def test_menu_seed_stable_ids(migrated_database_url: str) -> None:
    """菜单种子使用确定性 UUID，连续执行后 ID 不变。"""

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(migrated_database_url)

    async def _run() -> tuple[set[str], set[str]]:
        try:
            # 第一次执行
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                init = MenuSeedInitializer()
                await init.initialize(uow.session)
                await uow.commit()

            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT id::text FROM menu_menus"))
                ids1 = {row[0] for row in result.fetchall()}

            # 第二次执行
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                init = MenuSeedInitializer()
                await init.initialize(uow.session)
                await uow.commit()

            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT id::text FROM menu_menus"))
                ids2 = {row[0] for row in result.fetchall()}

            return ids1, ids2
        finally:
            await engine.dispose()

    ids1, ids2 = asyncio.run(_run())
    assert ids1 == ids2
    # 验证确定性 UUID
    assert _expected_menu_id("system") in ids1


# ── 测试: 字典种子初始化器 ─────────────────────────────────────────────────


@pytest.mark.g3
@pytest.mark.integration
def test_dict_seed_idempotent(migrated_database_url: str) -> None:
    """字典种子初始化器连续两次执行不产生重复。"""

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(migrated_database_url)

    async def _run() -> tuple[int, int, int, int]:
        try:
            # 第一次执行
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                init = DictSeedInitializer()
                await init.initialize(uow.session)
                await uow.commit()

            types1 = await _count_dict_types(engine)
            items1 = await _count_dict_items(engine)

            # 第二次执行
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                init = DictSeedInitializer()
                await init.initialize(uow.session)
                await uow.commit()

            types2 = await _count_dict_types(engine)
            items2 = await _count_dict_items(engine)

            return types1, items1, types2, items2
        finally:
            await engine.dispose()

    types1, items1, types2, items2 = asyncio.run(_run())
    assert types1 == types2
    assert items1 == items2
    assert types1 > 0
    assert items1 > 0


# ── 测试: 全部种子同步 ─────────────────────────────────────────────────────


@pytest.mark.g3
@pytest.mark.integration
def test_sync_seeds_all_initializers(migrated_database_url: str) -> None:
    """admin sync-seeds 执行全部已注册初始化器。"""

    from app.composition.modules import get_module_manifest
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(migrated_database_url)

    # 收集全部初始化器
    manifest = get_module_manifest()
    initializers: list[object] = []
    for module in manifest:
        initializers.extend(module.initializers)

    async def _run() -> tuple[int, int, int]:
        try:
            runner = InitializationRunner(initializers)  # type: ignore[arg-type]
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                await runner.run(uow.session)
                await uow.commit()

            menus = await _count_menus(engine)
            dict_types = await _count_dict_types(engine)
            dict_items = await _count_dict_items(engine)
            return menus, dict_types, dict_items
        finally:
            await engine.dispose()

    menus, dict_types, dict_items = asyncio.run(_run())
    assert menus > 0
    assert dict_types > 0
    assert dict_items > 0


@pytest.mark.g3
@pytest.mark.integration
def test_sync_seeds_double_run_idempotent(migrated_database_url: str) -> None:
    """admin sync-seeds 连续两次执行结果一致且不产生重复编码。"""

    from app.composition.modules import get_module_manifest
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(migrated_database_url)

    manifest = get_module_manifest()
    initializers: list[object] = []
    for module in manifest:
        initializers.extend(module.initializers)

    async def _run_sync() -> None:
        runner = InitializationRunner(initializers)  # type: ignore[arg-type]
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            await runner.run(uow.session)
            await uow.commit()

    async def _run() -> tuple[dict[str, int], dict[str, int]]:
        try:
            # 第一次执行
            await _run_sync()

            snapshot1 = {
                "menus": await _count_menus(engine),
                "dict_types": await _count_dict_types(engine),
                "dict_items": await _count_dict_items(engine),
            }

            # 第二次执行
            await _run_sync()

            snapshot2 = {
                "menus": await _count_menus(engine),
                "dict_types": await _count_dict_types(engine),
                "dict_items": await _count_dict_items(engine),
            }

            return snapshot1, snapshot2
        finally:
            await engine.dispose()

    snap1, snap2 = asyncio.run(_run())
    assert snap1 == snap2
    # 确保数据被创建
    assert snap1["menus"] > 0
    assert snap1["dict_types"] > 0
    assert snap1["dict_items"] > 0


@pytest.mark.g3
@pytest.mark.integration
def test_sync_seeds_no_duplicate_codes(migrated_database_url: str) -> None:
    """连续执行后菜单和字典编码无重复。"""

    from app.composition.modules import get_module_manifest
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(migrated_database_url)

    manifest = get_module_manifest()
    initializers: list[object] = []
    for module in manifest:
        initializers.extend(module.initializers)

    async def _run() -> tuple[int, int, int, int]:
        try:
            runner = InitializationRunner(initializers)  # type: ignore[arg-type]
            # 执行两次
            for _ in range(2):
                uow = SqlAlchemyUnitOfWork(engine)
                async with uow:
                    await runner.run(uow.session)
                    await uow.commit()

            # 检查无重复
            async with engine.connect() as conn:
                # 字典类型编码去重计数对比
                result = await conn.execute(
                    text("SELECT COUNT(DISTINCT code) FROM dict_types"),
                )
                distinct_codes = int(result.scalar() or 0)
                result = await conn.execute(
                    text("SELECT COUNT(*) FROM dict_types"),
                )
                total_codes = int(result.scalar() or 0)

                # 字典项 (dict_type_id, value) 去重计数
                result = await conn.execute(
                    text(
                        "SELECT COUNT(DISTINCT (dict_type_id::text, value)) "
                        "FROM dict_items",
                    ),
                )
                distinct_items = int(result.scalar() or 0)
                result = await conn.execute(
                    text("SELECT COUNT(*) FROM dict_items"),
                )
                total_items = int(result.scalar() or 0)

            return distinct_codes, total_codes, distinct_items, total_items
        finally:
            await engine.dispose()

    d_codes, t_codes, d_items, t_items = asyncio.run(_run())
    assert d_codes == t_codes
    assert d_items == t_items
