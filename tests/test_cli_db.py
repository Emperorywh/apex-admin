"""CLI db 命令测试 — SPEC 25.1.

覆盖验收标准:
  - db check 库可用返回 0、断库返回非 0。
  - db upgrade 仅执行 alembic upgrade head 且不创建业务数据。
  - 所有命令成功返回 0，参数错误返回 2。
"""

from __future__ import annotations

import pytest

from app.cli.__main__ import main as cli_main

# ── db check ──────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.integration
def test_cli_db_check_exit_zero_when_db_available(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """库可用时 db check 退出码 0（SPEC 25.1）。"""

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)
    exit_code = cli_main(["db", "check"])

    assert exit_code == 0
    captured = capsys.readouterr()
    # 输出中不应包含敏感配置（只显示连接状态）
    assert "DATABASE_URL" not in captured.out


@pytest.mark.g1
@pytest.mark.integration
def test_cli_db_check_nonzero_when_db_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """断库时 db check 退出码非 0（SPEC 25.1）。"""

    monkeypatch.setenv(
        "APEX_DATABASE_URL",
        "postgresql+psycopg://nobody@127.0.0.1:1/nonexistent?connect_timeout=3",
    )
    exit_code = cli_main(["db", "check"])

    assert exit_code != 0
    assert exit_code != 2  # 不是参数错误


@pytest.mark.g1
@pytest.mark.integration
def test_cli_db_upgrade_exit_zero(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """db upgrade 执行 alembic upgrade head 且退出码 0（SPEC 25.1）。

    SPEC 25.1: 不得隐式创建管理员或业务数据。
    """

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)
    exit_code = cli_main(["db", "upgrade"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "head" in captured.out.lower() or "迁移" in captured.out


@pytest.mark.g1
@pytest.mark.integration
def test_cli_db_upgrade_no_business_data(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """db upgrade 不创建业务数据（SPEC 25.1）。

    SPEC 25.1: "不得隐式创建管理员或业务数据"。
    迁移创建模块声明的表结构，但不插入业务演示数据。
    """

    from sqlalchemy import text

    from app.infrastructure.db.engine import create_db_engine

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)
    exit_code = cli_main(["db", "upgrade"])
    assert exit_code == 0

    # 同步检查数据库表（CLI 内部使用 asyncio.run，不能在 async 测试中调用）
    import asyncio

    async def _check_tables() -> set[str]:
        engine = create_db_engine(database_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
                    ),
                )
                return {row[0] for row in result.fetchall()}
        finally:
            await engine.dispose()

    tables = asyncio.run(_check_tables())
    # alembic_version 是迁移框架表，允许存在
    # 各 G1 模块声明的表由迁移创建，允许存在（当前: example_items）
    from app.composition.modules import get_module_manifest

    declared_tables: set[str] = {"alembic_version"}
    # 示例模块的表名约定为 example_items
    if any(m.code == "example" for m in get_module_manifest()):
        declared_tables.add("example_items")
    # 审计模块的表名约定为 audit_logs 和 login_logs
    if any(m.code == "audit" for m in get_module_manifest()):
        declared_tables.add("audit_logs")
        declared_tables.add("login_logs")
    # 用户模块的表名约定为 users
    if any(m.code == "identity" for m in get_module_manifest()):
        declared_tables.add("users")
    # 认证模块的表名约定
    if any(m.code == "auth" for m in get_module_manifest()):
        declared_tables.add("auth_sessions")
        declared_tables.add("auth_login_attempts")
        declared_tables.add("auth_refresh_tokens")
    # RBAC 模块的表名约定
    if any(m.code == "rbac" for m in get_module_manifest()):
        declared_tables.add("rbac_roles")
        declared_tables.add("rbac_permissions")
        declared_tables.add("rbac_role_permissions")
        declared_tables.add("rbac_user_roles")
    # 组织模块的表名约定
    if any(m.code == "org" for m in get_module_manifest()):
        declared_tables.add("org_departments")
        declared_tables.add("org_posts")
        declared_tables.add("org_user_departments")
        declared_tables.add("org_user_posts")
    # 菜单模块的表名约定
    if any(m.code == "menu" for m in get_module_manifest()):
        declared_tables.add("menu_menus")
        declared_tables.add("menu_role_menus")

    unexpected = tables - declared_tables
    assert unexpected == set(), f"db upgrade 发现未声明的表: {unexpected}"


# ── 参数错误退出码 ────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_cli_db_no_subcommand_exit_2() -> None:
    """db 无子命令时退出码 2。"""

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["db"])
    assert exc_info.value.code == 2


@pytest.mark.g1
@pytest.mark.unit
def test_cli_db_bad_arg_exit_2() -> None:
    """db 非法参数时退出码 2。"""

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["db", "check", "--bad-arg"])
    assert exc_info.value.code == 2
