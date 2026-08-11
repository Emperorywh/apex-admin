"""CLI 分支覆盖补充测试 — src/app/cli/__main__.py.

覆盖现有集成测试未触达的分支：
  - 各 _cmd_* 函数的 Settings() 加载失败路径
  - 各 _cmd_* 函数的 asyncio.run() 执行失败路径
  - 条件分支：clean_orphans 校验、生产环境拒绝、密钥缺失、密码策略
  - modules validate 的 Alembic 校验失败路径
  - db upgrade 的 CommandError / 通用异常路径
  - 纯函数 _mask_secret(None) / _read_password_from_stdin TTY 路径
  - _run_admin_sync_seeds 无初始化器短路
  - _run_create_admin 密码策略拒绝
"""

from __future__ import annotations

import argparse
import asyncio
import io
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from app.cli.__main__ import (
    _cmd_admin_sync_seeds,
    _cmd_audit_cleanup,
    _cmd_auth_create_admin,
    _cmd_auth_sync_permissions,
    _cmd_data_check,
    _cmd_db_check,
    _cmd_db_upgrade,
    _cmd_dev_seed_demo,
    _cmd_files_reconcile,
    _cmd_modules_validate,
    _cmd_sysconfig_re_encrypt,
    _mask_secret,
    _read_password_from_stdin,
    main,
)

# ── 辅助 ─────────────────────────────────────────────────────────────────


class _FakeStdin:
    """模拟管道标准输入."""

    def __init__(self, content: str) -> None:
        self._buf = io.StringIO(content)

    def isatty(self) -> bool:
        return False

    def readline(self) -> str:
        return self._buf.readline()


def _make_mock_settings() -> MagicMock:
    """构造含全部必要字段的 mock Settings 实例."""
    mock = MagicMock()
    mock.DATABASE_URL = "postgresql+psycopg://apex:apex@localhost:5432/apex"
    mock.ENVIRONMENT.value = "testing"
    mock.SYSCONFIG_ENCRYPTION_KEY = SecretStr("a" * 44)
    mock.SYSCONFIG_ENCRYPTION_KEY_PREVIOUS = None
    mock.FILE_STORAGE_ROOT = "/tmp/apex-files"
    mock.FILE_PENDING_TIMEOUT_HOURS = 1
    mock.FILE_TEMP_MAX_AGE_HOURS = 24
    mock.FILE_DELETION_DELAY_DAYS = 7
    mock.FILE_UNREFERENCED_RETENTION_DAYS = 7
    mock.AUDIT_LOG_RETENTION_DAYS = 90
    mock.LOGIN_LOG_RETENTION_DAYS = 90
    mock.SECURITY_EVENT_RETENTION_DAYS = 180
    return mock


def _patch_settings_ok(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Monkeypatch Settings 返回 mock 实例."""
    mock = _make_mock_settings()
    monkeypatch.setattr("app.cli.__main__.Settings", lambda: mock)
    return mock


def _patch_settings_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch Settings 抛出 RuntimeError."""

    def _raise() -> None:
        raise RuntimeError("模拟配置加载失败")

    monkeypatch.setattr("app.cli.__main__.Settings", _raise)


def _raising_func(*_args: object, **_kwargs: object) -> None:
    """通用 mock — 调用时抛出 RuntimeError."""
    raise RuntimeError("模拟运行失败")


# ── Settings 加载失败路径 ──────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_db_check_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """db check 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    assert _cmd_db_check() == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g1
@pytest.mark.unit
def test_db_upgrade_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """db upgrade 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    assert _cmd_db_upgrade() == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g2
@pytest.mark.unit
def test_auth_sync_permissions_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """auth sync-permissions 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    args = argparse.Namespace(clean_orphans=False, confirm=False)
    assert _cmd_auth_sync_permissions(args) == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g2
@pytest.mark.unit
def test_auth_create_admin_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """auth create-admin 在 Settings 加载失败时退出码 1."""

    monkeypatch.setattr("sys.stdin", _FakeStdin("TestPassword-123\n"))
    _patch_settings_fail(monkeypatch)
    args = argparse.Namespace(username="testadmin")
    assert _cmd_auth_create_admin(args) == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g1
@pytest.mark.unit
def test_dev_seed_demo_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dev seed-demo 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    assert _cmd_dev_seed_demo() == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_sysconfig_re_encrypt_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """sysconfig re-encrypt 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    assert _cmd_sysconfig_re_encrypt() == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_files_reconcile_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """files reconcile 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    args = argparse.Namespace(apply=False)
    assert _cmd_files_reconcile(args) == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_admin_sync_seeds_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """admin sync-seeds 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    assert _cmd_admin_sync_seeds() == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_data_check_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """data check 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    assert _cmd_data_check() == 1
    assert "配置加载失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_audit_cleanup_settings_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """audit cleanup 在 Settings 加载失败时退出码 1."""

    _patch_settings_fail(monkeypatch)
    args = argparse.Namespace(apply=False)
    assert _cmd_audit_cleanup(args) == 1
    assert "配置加载失败" in capsys.readouterr().err


# ── asyncio.run / 运行失败路径 ────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_db_check_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """db check 在 _check_db_connection 抛出异常时退出码 1."""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._check_db_connection", _raising_func)
    assert _cmd_db_check() == 1
    assert "数据库检查失败" in capsys.readouterr().err


@pytest.mark.g1
@pytest.mark.unit
def test_db_upgrade_command_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """db upgrade 在 alembic CommandError 时退出码 1."""

    from alembic.util.exc import CommandError

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr(
        "alembic.command.upgrade",
        lambda *args, **kwargs: (_ for _ in ()).throw(CommandError("test")),
    )
    assert _cmd_db_upgrade() == 1
    assert "数据库迁移失败" in capsys.readouterr().err


@pytest.mark.g1
@pytest.mark.unit
def test_db_upgrade_generic_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """db upgrade 在通用异常时退出码 1."""

    _patch_settings_ok(monkeypatch)

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("generic migration error")

    monkeypatch.setattr("alembic.command.upgrade", _raise)
    assert _cmd_db_upgrade() == 1
    assert "数据库迁移失败" in capsys.readouterr().err


@pytest.mark.g2
@pytest.mark.unit
def test_auth_sync_permissions_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """auth sync-permissions 在异步执行失败时退出码 1."""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_sync_permissions", _raising_func)
    args = argparse.Namespace(clean_orphans=False, confirm=False)
    assert _cmd_auth_sync_permissions(args) == 1
    assert "权限同步失败" in capsys.readouterr().err


@pytest.mark.g2
@pytest.mark.unit
def test_auth_create_admin_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """auth create-admin 在异步执行失败时退出码 1."""

    monkeypatch.setattr("sys.stdin", _FakeStdin("TestPassword-123\n"))
    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_create_admin", _raising_func)
    args = argparse.Namespace(username="testadmin")
    assert _cmd_auth_create_admin(args) == 1
    assert "创建管理员失败" in capsys.readouterr().err


@pytest.mark.g1
@pytest.mark.unit
def test_dev_seed_demo_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dev seed-demo 在异步执行失败时退出码 1."""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_dev_seed_demo", _raising_func)
    assert _cmd_dev_seed_demo() == 1
    assert "开发演示数据创建失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_sysconfig_re_encrypt_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """sysconfig re-encrypt 在异步执行失败时退出码 1."""

    mock = _patch_settings_ok(monkeypatch)
    mock.SYSCONFIG_ENCRYPTION_KEY_PREVIOUS = SecretStr("b" * 44)
    monkeypatch.setattr("app.cli.__main__._run_sysconfig_re_encrypt", _raising_func)
    assert _cmd_sysconfig_re_encrypt() == 1
    assert "密钥轮换重加密失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_files_reconcile_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """files reconcile 在异步执行失败时退出码 1."""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_files_reconcile", _raising_func)
    args = argparse.Namespace(apply=False)
    assert _cmd_files_reconcile(args) == 1
    assert "文件一致性恢复失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_admin_sync_seeds_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """admin sync-seeds 在异步执行失败时退出码 1."""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_admin_sync_seeds", _raising_func)
    assert _cmd_admin_sync_seeds() == 1
    assert "种子同步失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_data_check_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """data check 在异步执行失败时退出码 1."""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_data_check", _raising_func)
    assert _cmd_data_check() == 1
    assert "数据检查失败" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_audit_cleanup_async_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """audit cleanup 在异步执行失败时退出码 1."""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_audit_cleanup", _raising_func)
    args = argparse.Namespace(apply=False)
    assert _cmd_audit_cleanup(args) == 1
    assert "审计日志清理失败" in capsys.readouterr().err


# ── 条件分支测试 ──────────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_auth_sync_permissions_clean_without_confirm(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """auth sync-permissions --clean-orphans 无 --confirm 时退出码 1."""

    args = argparse.Namespace(clean_orphans=True, confirm=False)
    assert _cmd_auth_sync_permissions(args) == 1
    assert "--confirm" in capsys.readouterr().err


@pytest.mark.g1
@pytest.mark.unit
def test_dev_seed_demo_production_rejected(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dev seed-demo 在生产环境被拒绝（SPEC 8.5）。"""

    mock = _patch_settings_ok(monkeypatch)
    mock.ENVIRONMENT.value = "production"
    assert _cmd_dev_seed_demo() == 1
    assert "生产环境" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_sysconfig_re_encrypt_no_current_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """sysconfig re-encrypt 在当前密钥为空时退出码 1。"""

    mock = _patch_settings_ok(monkeypatch)
    mock.SYSCONFIG_ENCRYPTION_KEY = SecretStr("")
    assert _cmd_sysconfig_re_encrypt() == 1
    assert "SYSCONFIG_ENCRYPTION_KEY 未设置" in capsys.readouterr().err


@pytest.mark.g3
@pytest.mark.unit
def test_sysconfig_re_encrypt_no_previous_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """sysconfig re-encrypt 在前一代密钥未设置时退出码 1（SPEC 23.2）。"""

    _patch_settings_ok(monkeypatch)
    assert _cmd_sysconfig_re_encrypt() == 1
    assert "SYSCONFIG_ENCRYPTION_KEY_PREVIOUS 未设置" in capsys.readouterr().err


@pytest.mark.g2
@pytest.mark.unit
def test_run_create_admin_password_too_short(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_run_create_admin 在密码不满足长度策略时退出码 1（SPEC 23.2）。

    密码策略校验在数据库操作之前执行，不需要真实数据库连接。
    """

    from app.cli.__main__ import _run_create_admin

    exit_code = asyncio.run(
        _run_create_admin(
            "postgresql+psycopg://fake@localhost/fake",
            username="testadmin",
            password="short",
        ),
    )
    assert exit_code == 1
    assert "密码策略校验失败" in capsys.readouterr().err


# ── modules validate Alembic 失败路径 ─────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_modules_validate_alembic_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """modules validate 在 Alembic RuntimeError（多 head）时退出码 1。"""

    def _raise_runtime(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("multiple heads detected")

    monkeypatch.setattr(
        "app.infrastructure.db.migrations.get_head_revision",
        _raise_runtime,
    )
    assert _cmd_modules_validate() == 1
    assert "Alembic 校验失败" in capsys.readouterr().err


@pytest.mark.g1
@pytest.mark.unit
def test_modules_validate_alembic_generic_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """modules validate 在 Alembic 通用异常时退出码 1。"""

    def _raise_value(*_args: object, **_kwargs: object) -> None:
        raise ValueError("unexpected config")

    monkeypatch.setattr(
        "app.infrastructure.db.migrations.get_head_revision",
        _raise_value,
    )
    assert _cmd_modules_validate() == 1
    assert "Alembic 校验出错" in capsys.readouterr().err


# ── 纯函数测试 ────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_mask_secret_none_returns_placeholder() -> None:
    """_mask_secret(None) 返回 <未设置>。"""

    assert _mask_secret(None) == "<未设置>"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_secret_value_returns_mask() -> None:
    """_mask_secret(SecretStr) 返回固定掩码。"""

    assert _mask_secret(SecretStr("anything")) == "**********"


@pytest.mark.g2
@pytest.mark.unit
def test_read_password_from_stdin_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_read_password_from_stdin 在 TTY 模式下使用 getpass 读取密码。"""

    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "tty-password")

    password = _read_password_from_stdin()
    assert password == "tty-password"


# ── _run_admin_sync_seeds 内部分支 ────────────────────────────────────────


@pytest.mark.g3
@pytest.mark.unit
def test_run_admin_sync_seeds_no_initializers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_run_admin_sync_seeds 在无初始化器时短路返回 0（不触碰数据库）。"""

    from app.cli.__main__ import _run_admin_sync_seeds

    monkeypatch.setattr(
        "app.composition.modules.get_module_manifest",
        lambda: [],
    )

    exit_code = asyncio.run(
        _run_admin_sync_seeds("postgresql+psycopg://fake@localhost/fake"),
    )
    assert exit_code == 0
    assert "无已注册" in capsys.readouterr().out


# ── main() 分发验证 ───────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_main_dispatches_config_show(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main 正确分发 config show 命令。"""

    _patch_settings_ok(monkeypatch)
    exit_code = main(["config", "show"])
    assert exit_code == 0


@pytest.mark.g3
@pytest.mark.unit
def test_main_dispatches_sysconfig_re_encrypt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main 正确分发 sysconfig re-encrypt 命令。"""

    _patch_settings_ok(monkeypatch)
    exit_code = main(["sysconfig", "re-encrypt"])
    # 前一代密钥未设置 → 退出码 1（但分发逻辑本身已执行）
    assert exit_code == 1


@pytest.mark.g3
@pytest.mark.unit
def test_main_dispatches_files_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main 正确分发 files reconcile 命令。"""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_files_reconcile", _raising_func)
    exit_code = main(["files", "reconcile", "--dry-run"])
    assert exit_code == 1


@pytest.mark.g3
@pytest.mark.unit
def test_main_dispatches_audit_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main 正确分发 audit cleanup 命令。"""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_audit_cleanup", _raising_func)
    exit_code = main(["audit", "cleanup"])
    assert exit_code == 1


@pytest.mark.g3
@pytest.mark.unit
def test_main_dispatches_admin_sync_seeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main 正确分发 admin sync-seeds 命令。"""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_admin_sync_seeds", _raising_func)
    exit_code = main(["admin", "sync-seeds"])
    assert exit_code == 1


@pytest.mark.g3
@pytest.mark.unit
def test_main_dispatches_data_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main 正确分发 data check 命令。"""

    _patch_settings_ok(monkeypatch)
    monkeypatch.setattr("app.cli.__main__._run_data_check", _raising_func)
    exit_code = main(["data", "check"])
    assert exit_code == 1


@pytest.mark.g1
@pytest.mark.unit
def test_main_dispatches_dev_seed_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main 正确分发 dev seed-demo 命令。"""

    mock = _patch_settings_ok(monkeypatch)
    mock.ENVIRONMENT.value = "production"
    exit_code = main(["dev", "seed-demo"])
    # 生产环境拒绝 → 退出码 1（分发逻辑已执行）
    assert exit_code == 1


@pytest.mark.g1
@pytest.mark.unit
def test_main_dispatches_db_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main 正确分发 db upgrade 命令。"""

    _patch_settings_ok(monkeypatch)

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("test")

    monkeypatch.setattr("alembic.command.upgrade", _raise)
    exit_code = main(["db", "upgrade"])
    assert exit_code == 1
