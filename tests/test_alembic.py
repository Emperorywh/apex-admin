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
from app.infrastructure.db.migrations import get_head_revision

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

    # 创建独立空数据库避免与其他测试的迁移状态冲突
    db_name = "apex_test_mig_head"
    server_url = database_url.rsplit("/", 1)[0]
    await _create_empty_database(database_url, db_name)
    empty_db_url = f"{server_url}/{db_name}"

    try:
        # 设置环境变量使 env.py 中的 Settings() 加载正确的 URL
        monkeypatch.setenv("APEX_DATABASE_URL", empty_db_url)

        # 使用 get_alembic_config 确保版本目录正确设置（含默认 versions）
        from app.composition.modules import MODULE_VERSION_LOCATIONS
        from app.infrastructure.db.migrations import get_alembic_config

        def _upgrade() -> None:
            config = get_alembic_config(
                database_url=empty_db_url,
                version_locations=MODULE_VERSION_LOCATIONS,
            )
            command.upgrade(config, "head")

        # 在线程中执行避免 asyncio.run 与 pytest 事件循环冲突
        await asyncio.to_thread(_upgrade)

        # 验证 alembic_version 表存在且包含预期 head revision
        engine = create_db_engine(empty_db_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT version_num FROM alembic_version"),
                )
                row = result.fetchone()
                assert row is not None
                assert row[0] == "0008_org_departments"

                # 验证示例模块表已创建
                table_result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_name = 'example_items'",
                    ),
                )
                assert table_result.fetchone() is not None
        finally:
            await engine.dispose()
    finally:
        await _drop_database(database_url, db_name)


@pytest.mark.g1
@pytest.mark.integration
async def test_alembic_single_head() -> None:
    """alembic heads 恰好输出一个 head（SPEC 8.2）。

    包含示例模块迁移版本目录后仍只有一个 head。
    """

    from alembic.script import ScriptDirectory

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(version_locations=MODULE_VERSION_LOCATIONS)
    script_dir = ScriptDirectory.from_config(config)
    heads = script_dir.get_heads()

    assert len(heads) == 1, f"Expected exactly one head, got {heads}"
    assert heads[0] == "0008_org_departments"


@pytest.mark.g1
@pytest.mark.unit
def test_env_py_collects_version_locations_from_registry() -> None:
    """env.py 仅从模块注册表收集 version_locations（SPEC 8.2）。

    验证模块注册表存在且为列表类型。
    示例模块注册后包含其迁移版本目录。
    """

    assert isinstance(MODULE_VERSION_LOCATIONS, list)
    # 示例模块已注册，包含至少一个迁移版本目录
    assert len(MODULE_VERSION_LOCATIONS) >= 1


@pytest.mark.g1
@pytest.mark.unit
def test_head_revision_includes_example_module() -> None:
    """全局 head revision 包含已注册模块迁移（SPEC 8.2 / 30.2）。

    示例模块迁移 0002_example_items 的 down_revision 指向 0001_initial，
    审计模块迁移 0003_audit_tables 的 down_revision 指向 0002_example_items，
    用户模块迁移 0004_users 的 down_revision 指向 0003_audit_tables，
    认证模块迁移 0005_auth_tables 的 down_revision 指向 0004_users，
    Refresh Token 迁移 0006_refresh_tokens 的 down_revision 指向 0005_auth_tables，
    RBAC 迁移 0007_rbac_tables 的 down_revision 指向 0006_refresh_tokens，
    组织迁移 0008_org_departments 的 down_revision 指向 0007_rbac_tables，
    组成全局单头 revision 图。
    """

    from app.composition.modules import MODULE_VERSION_LOCATIONS

    head = get_head_revision(MODULE_VERSION_LOCATIONS)
    assert head == "0008_org_departments"
