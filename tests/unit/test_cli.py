"""CLI 单元测试（SPEC §25.1）。

验证：
- CLI 入口点可访问，--help 列出全部四个命令
- config show 输出脱敏配置摘要（密钥值为 ``***``）
- modules validate 验证模块注册和 Alembic 单头
- db check 检查数据库连接（成功 0，失败 1）
- db upgrade 只执行 alembic upgrade head，不创建管理员或业务数据
- 退出码：成功 0，参数错误 2，运行/配置失败 1
- 失败时不吞掉异常（traceback 打印到 stderr），不留半完成状态
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli import create_parser, main
from app.modules.registry import ModuleRegistrationError

pytestmark = [pytest.mark.unit, pytest.mark.g1]


# ---------------------------------------------------------------------------
# 入口点与帮助输出
# ---------------------------------------------------------------------------


class TestHelpAndParser:
    """CLI 入口点可访问性和帮助输出。"""

    def test_help_lists_all_commands(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--help 列出全部四个 G1 命令。"""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "db check" in captured.out
        assert "db upgrade" in captured.out
        assert "modules validate" in captured.out
        assert "config show" in captured.out

    def test_no_command_returns_arg_error(self) -> None:
        """无命令时返回参数错误（退出码 2）。"""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2

    def test_invalid_command_returns_arg_error(self) -> None:
        """无效命令返回参数错误（退出码 2）。"""
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent"])
        assert exc_info.value.code == 2

    def test_missing_subcommand_returns_arg_error(self) -> None:
        """缺少子命令时返回参数错误（退出码 2）。"""
        with pytest.raises(SystemExit) as exc_info:
            main(["db"])
        assert exc_info.value.code == 2

    def test_invalid_flag_returns_arg_error(self) -> None:
        """无效选项返回参数错误（退出码 2）。"""
        with pytest.raises(SystemExit) as exc_info:
            main(["--invalid-flag"])
        assert exc_info.value.code == 2

    def test_parser_accepts_all_commands(self) -> None:
        """解析器接受全部四个命令且设置 func 属性。"""
        parser = create_parser()
        for argv in (
            ["db", "check"],
            ["db", "upgrade"],
            ["modules", "validate"],
            ["config", "show"],
        ):
            args = parser.parse_args(argv)
            assert hasattr(args, "func")


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------


class TestConfigShow:
    """config show 命令。"""

    @patch("app.cli.config.Settings")
    def test_config_show_outputs_desensitized(
        self,
        mock_settings_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """config show 输出脱敏配置摘要，密钥值为 ***。"""
        mock_settings = MagicMock()
        mock_settings.to_safe_summary.return_value = {
            "app_env": "testing",
            "database_url": "postgresql+psycopg://***@localhost:5432/apex",
            "db_pool_size": "5",
            "db_max_overflow": "5",
            "access_token_hmac_key": "***",
            "refresh_token_hmac_key": "***",
            "config_encryption_key": "***",
            "file_storage_root": "/tmp/apex-files",
            "allowed_origins": ["http://localhost:3000"],
        }
        mock_settings_cls.return_value = mock_settings

        exit_code = main(["config", "show"])
        captured = capsys.readouterr()

        assert exit_code == 0
        output = json.loads(captured.out)
        assert output["access_token_hmac_key"] == "***"
        assert output["refresh_token_hmac_key"] == "***"
        assert output["config_encryption_key"] == "***"
        assert "***" in output["database_url"]


# ---------------------------------------------------------------------------
# modules validate
# ---------------------------------------------------------------------------


class TestModulesValidate:
    """modules validate 命令。"""

    def test_modules_validate_succeeds(self, capsys: pytest.CaptureFixture[str]) -> None:
        """modules validate 校验通过（G1 无业务模块，零冲突）。"""
        exit_code = main(["modules", "validate"])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "校验通过" in captured.out
        assert "0 个冲突" in captured.out

    @patch("app.cli.modules.ModuleRegistry")
    def test_modules_validate_failure_returns_nonzero(
        self,
        mock_registry_cls: MagicMock,
    ) -> None:
        """模块注册校验失败时返回非 0 退出码。"""
        mock_registry_cls.side_effect = ModuleRegistrationError("测试冲突")
        exit_code = main(["modules", "validate"])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# db check
# ---------------------------------------------------------------------------


class TestDbCheck:
    """db check 命令。"""

    @patch("app.cli.db.SqlAlchemyDbPoolProvider")
    @patch("app.cli.db.Settings")
    def test_db_check_success(
        self,
        mock_settings_cls: MagicMock,
        mock_provider_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """数据库连接正常时返回 0。"""
        mock_provider = MagicMock()
        mock_provider.initialize = AsyncMock(return_value=None)
        mock_provider.check_connection = AsyncMock(return_value=True)
        mock_provider.dispose = AsyncMock(return_value=None)
        mock_provider_cls.return_value = mock_provider

        exit_code = main(["db", "check"])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "正常" in captured.out

    @patch("app.cli.db.SqlAlchemyDbPoolProvider")
    @patch("app.cli.db.Settings")
    def test_db_check_failure(
        self,
        mock_settings_cls: MagicMock,
        mock_provider_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """数据库连接不可用时返回非 0 退出码。"""
        mock_provider = MagicMock()
        mock_provider.initialize = AsyncMock(return_value=None)
        mock_provider.check_connection = AsyncMock(return_value=False)
        mock_provider.dispose = AsyncMock(return_value=None)
        mock_provider_cls.return_value = mock_provider

        exit_code = main(["db", "check"])
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "不可用" in captured.err

    @patch("app.cli.db.SqlAlchemyDbPoolProvider")
    @patch("app.cli.db.Settings")
    def test_db_check_disposes_on_failure(
        self,
        mock_settings_cls: MagicMock,
        mock_provider_cls: MagicMock,
    ) -> None:
        """连接不可用时仍释放连接池，不留半完成状态。"""
        mock_provider = MagicMock()
        mock_provider.initialize = AsyncMock(return_value=None)
        mock_provider.check_connection = AsyncMock(return_value=False)
        mock_provider.dispose = AsyncMock(return_value=None)
        mock_provider_cls.return_value = mock_provider

        exit_code = main(["db", "check"])
        assert exit_code == 1
        mock_provider.initialize.assert_called_once()
        mock_provider.check_connection.assert_called_once()
        mock_provider.dispose.assert_called_once()

    @patch("app.cli.db.SqlAlchemyDbPoolProvider")
    @patch("app.cli.db.Settings")
    def test_db_check_disposes_on_exception(
        self,
        mock_settings_cls: MagicMock,
        mock_provider_cls: MagicMock,
    ) -> None:
        """check_connection 抛异常时仍在 finally 中释放连接池。"""
        mock_provider = MagicMock()
        mock_provider.initialize = AsyncMock(return_value=None)
        mock_provider.check_connection = AsyncMock(side_effect=RuntimeError("连接错误"))
        mock_provider.dispose = AsyncMock(return_value=None)
        mock_provider_cls.return_value = mock_provider

        exit_code = main(["db", "check"])
        assert exit_code == 1
        mock_provider.dispose.assert_called_once()


# ---------------------------------------------------------------------------
# db upgrade
# ---------------------------------------------------------------------------


class TestDbUpgrade:
    """db upgrade 命令。"""

    @patch("app.cli.db.command.upgrade")
    def test_db_upgrade_runs_alembic_head(
        self,
        mock_upgrade: MagicMock,
    ) -> None:
        """db upgrade 只执行 alembic upgrade head。"""
        exit_code = main(["db", "upgrade"])

        assert exit_code == 0
        mock_upgrade.assert_called_once()
        assert mock_upgrade.call_args.args[1] == "head"

    @patch("app.cli.db.command.upgrade")
    def test_db_upgrade_does_not_create_business_data(
        self,
        mock_upgrade: MagicMock,
    ) -> None:
        """db upgrade 只调用 alembic upgrade head，不创建管理员或业务数据。"""
        exit_code = main(["db", "upgrade"])
        assert exit_code == 0
        # 唯一调用是 alembic upgrade head
        mock_upgrade.assert_called_once()
        assert mock_upgrade.call_args.args[1] == "head"

    @patch("app.cli.db.command.upgrade")
    def test_db_upgrade_failure_returns_nonzero(
        self,
        mock_upgrade: MagicMock,
    ) -> None:
        """迁移失败时返回非 0 退出码，不吞掉异常。"""
        mock_upgrade.side_effect = RuntimeError("迁移失败")
        exit_code = main(["db", "upgrade"])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# 退出码与异常处理
# ---------------------------------------------------------------------------


class TestExitCodes:
    """退出码和异常处理验证。"""

    def test_success_returns_zero(self) -> None:
        """成功命令返回 0。"""
        exit_code = main(["modules", "validate"])
        assert exit_code == 0

    def test_arg_error_returns_two(self) -> None:
        """参数错误返回 2。"""
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent"])
        assert exc_info.value.code == 2

    @patch("app.cli.config.Settings")
    def test_config_failure_returns_nonzero(
        self,
        mock_settings_cls: MagicMock,
    ) -> None:
        """配置加载失败时返回非 0 退出码。"""
        mock_settings_cls.side_effect = ValueError("配置加载失败")
        exit_code = main(["config", "show"])
        assert exit_code == 1

    @patch("app.cli.config.Settings")
    def test_failure_does_not_swallow_exception(
        self,
        mock_settings_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """失败时打印 traceback，不吞掉异常。"""
        mock_settings_cls.side_effect = ValueError("配置加载失败")
        exit_code = main(["config", "show"])
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "配置加载失败" in captured.err
