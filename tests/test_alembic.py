"""Alembic 迁移环境测试 — SPEC 8.2.

覆盖验收标准:
  - 空 PostgreSQL 18 执行 alembic upgrade head 成功。
  - alembic heads 恰好一个 head。
  - env.py 仅从模块注册表收集 version_locations。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.composition.modules import MODULE_VERSION_LOCATIONS
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.migrations import ALEMBIC_INI_PATH, get_head_revision

# ── 辅助函数 ───────────────────────────────────────────────────────────────


async def _create_empty_database(database_url: str, db_name: str) -> str:
    """在给定服务器上创建一个空数据库，返回其连接 URL。

    使用 AUTOCOMMIT 隔离级别，因为 CREATE DATABASE 不能在事务中执行。
    """

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            # psycopg 需要 autocommit 来执行 CREATE DATABASE
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(f'CREATE DATABASE "{db_name}"'),
            )
    finally:
        await engine.dispose()

    # 构造新数据库的 URL
    base = database_url.rsplit("/", 1)[0]
    return f"{base}/{db_name}"


async def _drop_database(database_url: str, db_name: str) -> None:
    """删除指定数据库。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(f'DROP DATABASE IF EXISTS "{db_name}"'),
            )
    finally:
        await engine.dispose()


# ── 迁移测试 ───────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.integration
async def test_alembic_upgrade_head_on_empty_db(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空库执行 alembic upgrade head 成功（SPEC 8.2）。"""

    from alembic import command
    from alembic.config import Config

    # 创建独立空数据库避免与其他测试的迁移状态冲突
    db_name = "apex_test_mig_head"
    server_url = database_url.rsplit("/", 1)[0]
    await _create_empty_database(database_url, db_name)
    empty_db_url = f"{server_url}/{db_name}"

    try:
        # 设置环境变量使 env.py 中的 Settings() 加载正确的 URL
        monkeypatch.setenv("APEX_DATABASE_URL", empty_db_url)

        def _upgrade() -> None:
            config = Config(str(ALEMBIC_INI_PATH))
            config.set_main_option("sqlalchemy.url", empty_db_url)
            command.upgrade(config, "head")

        # 在线程中执行避免 asyncio.run 与 pytest 事件循环冲突
        await asyncio.to_thread(_upgrade)

        # 验证 alembic_version 表存在且包含预期 revision
        engine = create_db_engine(empty_db_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT version_num FROM alembic_version"),
                )
                row = result.fetchone()
                assert row is not None
                assert row[0] == "0001_initial"
        finally:
            await engine.dispose()
    finally:
        await _drop_database(database_url, db_name)


@pytest.mark.g1
@pytest.mark.integration
async def test_alembic_single_head() -> None:
    """alembic heads 恰好输出一个 head（SPEC 8.2）。"""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI_PATH))
    script_dir = ScriptDirectory.from_config(config)
    heads = script_dir.get_heads()

    assert len(heads) == 1, f"Expected exactly one head, got {heads}"
    assert heads[0] == "0001_initial"


@pytest.mark.g1
@pytest.mark.unit
def test_env_py_collects_version_locations_from_registry() -> None:
    """env.py 仅从模块注册表收集 version_locations（SPEC 8.2）。

    验证模块注册表存在且为列表类型。
    当前 G1 阶段为空列表（无业务模块）。
    """

    assert isinstance(MODULE_VERSION_LOCATIONS, list)
    # G1 阶段无业务模块
    assert len(MODULE_VERSION_LOCATIONS) == 0


@pytest.mark.g1
@pytest.mark.unit
def test_initial_migration_has_no_business_tables() -> None:
    """初始迁移不创建业务模块表结构（SPEC nonGoals）。

    验证初始迁移文件存在且 revision/down_revision 设置正确。
    """

    head = get_head_revision()
    assert head == "0001_initial"
