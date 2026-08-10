"""CLI auth 命令测试 — SPEC 25.2 / 23.2.

覆盖验收标准:
  - AC-0: auth create-admin 经受控标准输入创建管理员成功，
          命令输出与日志中不存在密码；重复执行不创建第二个同名管理员。
  - AC-4: Token HMAC 密钥轮换命令支持双密钥短期切换。
  - AC-4: 命令退出码规范：成功 0、参数错误 2、运行失败非 0。
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.cli.__main__ import main as cli_main

if TYPE_CHECKING:
    from collections.abc import Iterator


# ── 辅助 ───────────────────────────────────────────────────────────────────


class _FakeStdin:
    """模拟管道标准输入 — 提供 ``isatty`` 和 ``readline``."""

    def __init__(self, content: str) -> None:
        self._buf = io.StringIO(content)

    def isatty(self) -> bool:
        return False

    def readline(self) -> str:
        return self._buf.readline()


@pytest.fixture
def cli_env(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """提供 CLI 测试环境 — 设置数据库 URL 并应用迁移."""

    import asyncio

    from alembic import command

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)
    monkeypatch.setenv("APEX_ENVIRONMENT", "testing")

    # 应用迁移
    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    asyncio.run(asyncio.to_thread(lambda: command.upgrade(config, "head")))

    yield database_url

    # 清理
    async def _cleanup() -> None:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM rbac_user_roles"))
                await conn.execute(text("DELETE FROM rbac_role_permissions"))
                await conn.execute(text("DELETE FROM rbac_roles"))
                await conn.execute(text("DELETE FROM rbac_permissions"))
                await conn.execute(text("DELETE FROM auth_refresh_tokens"))
                await conn.execute(text("DELETE FROM auth_sessions"))
                await conn.execute(text("DELETE FROM auth_login_attempts"))
                await conn.execute(text("DELETE FROM audit_logs"))
                await conn.execute(text("DELETE FROM users"))
        finally:
            await engine.dispose()

    asyncio.run(_cleanup())


async def _count_admins(database_url: str, username: str) -> int:
    """查询指定用户名的管理员数量."""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM users WHERE username = :u"),
                {"u": username},
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _has_super_admin_role(database_url: str, username: str) -> bool:
    """检查用户是否拥有 super_admin 角色."""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM rbac_user_roles ur "
                    "JOIN rbac_roles r ON r.id = ur.role_id "
                    "JOIN users u ON u.id = ur.user_id "
                    "WHERE u.username = :u AND r.code = 'super_admin'",
                ),
                {"u": username},
            )
            return int(result.scalar() or 0) > 0
    finally:
        await engine.dispose()


# ── create-admin 测试 ─────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.integration
def test_create_admin_success_via_stdin(
    cli_env: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """create-admin 经标准输入创建管理员成功 — AC-0."""

    import asyncio

    password = "TestAdmin-Password-123"
    username = f"admin_{uuid4().hex[:8]}"

    monkeypatch.setattr("sys.stdin", _FakeStdin(f"{password}\n"))

    exit_code = cli_main(["auth", "create-admin", "--username", username])
    assert exit_code == 0

    captured = capsys.readouterr()
    # SPEC 23.2: 输出中不存在密码明文
    assert password not in captured.out
    assert password not in captured.err

    # 验证用户已在数据库中创建
    count = asyncio.run(_count_admins(cli_env, username))
    assert count == 1

    # 验证已分配 super_admin 角色
    has_role = asyncio.run(_has_super_admin_role(cli_env, username))
    assert has_role


@pytest.mark.g2
@pytest.mark.integration
def test_create_admin_idempotent(
    cli_env: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """create-admin 重复执行不创建第二个同名管理员 — AC-0."""

    import asyncio

    password = "TestAdmin-Password-123"
    username = f"admin_{uuid4().hex[:8]}"

    # 第一次创建
    monkeypatch.setattr("sys.stdin", _FakeStdin(f"{password}\n"))
    exit1 = cli_main(["auth", "create-admin", "--username", username])
    assert exit1 == 0

    # 第二次创建（相同用户名）— 幂等
    monkeypatch.setattr("sys.stdin", _FakeStdin(f"{password}\n"))
    exit2 = cli_main(["auth", "create-admin", "--username", username])
    assert exit2 == 0

    captured = capsys.readouterr()
    assert "已存在" in captured.out

    # 仍然只有一个同名管理员
    count = asyncio.run(_count_admins(cli_env, username))
    assert count == 1


@pytest.mark.g2
@pytest.mark.integration
def test_create_admin_password_not_in_output(
    cli_env: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """create-admin 输出与日志中不存在密码 — AC-0 / SPEC 23.2."""

    password = "TestAdmin-Password-123"
    username = f"admin_{uuid4().hex[:8]}"

    monkeypatch.setattr("sys.stdin", _FakeStdin(f"{password}\n"))
    exit_code = cli_main(["auth", "create-admin", "--username", username])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err


@pytest.mark.g2
@pytest.mark.unit
def test_create_admin_missing_username_exit_2() -> None:
    """create-admin 缺少 --username 参数时退出码 2 — AC-4."""

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "create-admin"])
    assert exc_info.value.code == 2


@pytest.mark.g2
@pytest.mark.unit
def test_create_admin_no_password_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create-admin 密码为空时退出码非 0 — AC-4."""

    monkeypatch.setattr("sys.stdin", _FakeStdin("\n"))
    exit_code = cli_main(
        ["auth", "create-admin", "--username", "testadmin"],
    )
    assert exit_code != 0
    assert exit_code != 2


# ── rotate-token-keys 测试 ────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_rotate_token_keys_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """rotate-token-keys 生成新密钥并输出轮换说明 — AC-3 / SPEC 23.2."""

    exit_code = cli_main(["auth", "rotate-token-keys"])
    assert exit_code == 0

    captured = capsys.readouterr()
    # 输出应包含轮换步骤说明
    assert "APEX_ACCESS_TOKEN_HMAC_KEY" in captured.out
    assert "APEX_REFRESH_TOKEN_HMAC_KEY" in captured.out
    assert "_PREVIOUS" in captured.out
    assert "KEY_ROTATION_EXPIRES_AT" in captured.out
    # 应包含具体的新密钥值
    assert "=" in captured.out


# ── 命令退出码规范测试 ────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_auth_no_subcommand_exit_2() -> None:
    """auth 无子命令时退出码 2 — AC-4."""

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth"])
    assert exc_info.value.code == 2


@pytest.mark.g2
@pytest.mark.unit
def test_auth_bad_subcommand_exit_2() -> None:
    """auth 非法子命令时退出码 2 — AC-4."""

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "badcommand"])
    assert exc_info.value.code == 2
