"""auth CLI 命令单元测试（SPEC §25.2、VERIFY-078、VERIFY-079）。

使用 mock 替代数据库连接，验证 CLI 命令的行为正确性：

- ``auth create-admin``：密码隐藏输入、不在参数/输出/日志中、幂等
- ``auth sync-permissions``：输出格式、孤立权限报告、幂等
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli.auth import (
    _async_create_admin,
    _async_sync_permissions,
    _read_input,
    _read_password,
    auth_create_admin,
    auth_sync_permissions,
)

pytestmark = [pytest.mark.unit, pytest.mark.g2]


# ===========================================================================
# _read_input 辅助函数
# ===========================================================================


class TestReadInput:
    """_read_input 读取标准输入。"""

    def test_returns_stripped_value(self) -> None:
        """正常读取并去除首尾空白。"""
        with patch("builtins.input", return_value="  hello  "):
            assert _read_input("提示: ") == "hello"

    def test_returns_empty_on_eof(self) -> None:
        """EOF 时返回空字符串。"""
        with patch("builtins.input", side_effect=EOFError):
            assert _read_input("提示: ") == ""


# ===========================================================================
# _read_password 辅助函数
# ===========================================================================


class TestReadPassword:
    """_read_password 使用 getpass 隐藏密码输入。"""

    def test_uses_getpass(self) -> None:
        """密码通过 getpass.getpass 读取（隐藏输入）。"""
        with patch("app.cli.auth.getpass.getpass", return_value="secret123") as mock_getpass:
            result = _read_password()
            mock_getpass.assert_called_once()
            assert result == "secret123"

    def test_password_not_in_args(self) -> None:
        """密码不接受为命令行参数（仅通过 getpass 读取）。"""
        import inspect

        sig = inspect.signature(auth_create_admin)
        assert len(sig.parameters) == 0, "auth_create_admin 不应接受命令行参数"


# ===========================================================================
# auth create-admin
# ===========================================================================


def _make_mock_settings() -> MagicMock:
    """构造 mock Settings。"""
    return MagicMock()


def _make_mock_provider(engine: MagicMock | None = None) -> MagicMock:
    """构造 mock SqlAlchemyDbPoolProvider。"""
    provider = MagicMock()
    provider.initialize = AsyncMock(return_value=None)
    provider.dispose = AsyncMock(return_value=None)
    provider.engine = engine or MagicMock()
    return provider


class TestCreateAdminValidation:
    """create-admin 输入校验。"""

    async def test_empty_username_returns_failure(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空用户名时返回退出码 1。"""
        settings = _make_mock_settings()
        with (
            patch("app.cli.auth._read_input", side_effect=["", "name"]),
            patch("app.cli.auth._read_password", return_value="P@ssw0rd1234"),
        ):
            result = await _async_create_admin(settings)

        assert result == 1
        captured = capsys.readouterr()
        assert "用户名不能为空" in captured.err

    async def test_empty_password_returns_failure(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空密码时返回退出码 1。"""
        settings = _make_mock_settings()
        with (
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value=""),
        ):
            result = await _async_create_admin(settings)

        assert result == 1
        captured = capsys.readouterr()
        assert "密码不能为空" in captured.err

    async def test_invalid_username_policy_returns_failure(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """用户名策略校验失败时返回退出码 1。"""
        settings = _make_mock_settings()
        with (
            patch("app.cli.auth._read_input", side_effect=["ab", "Admin"]),
            patch("app.cli.auth._read_password", return_value="P@ssw0rd1234"),
        ):
            result = await _async_create_admin(settings)

        assert result == 1

    async def test_invalid_password_policy_returns_failure(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """密码策略校验失败时返回退出码 1。"""
        settings = _make_mock_settings()
        with (
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value="weak"),
        ):
            result = await _async_create_admin(settings)

        assert result == 1


class TestCreateAdminIdempotency:
    """create-admin 幂等性。"""

    async def test_existing_user_skips_creation(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """用户名已存在时跳过创建，返回退出码 0。"""
        settings = _make_mock_settings()
        mock_engine = MagicMock()
        provider = _make_mock_provider(mock_engine)

        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)
        mock_uow.session = MagicMock()

        existing_user = MagicMock()

        with (
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyUserUnitOfWork") as mock_uow_cls,
            patch("app.cli.auth.SqlAlchemyUserRepository") as mock_repo_cls,
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value="P@ssw0rd1234"),
        ):
            mock_uow_cls.return_value = mock_uow
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_username = AsyncMock(return_value=existing_user)

            result = await _async_create_admin(settings)

        assert result == 0
        captured = capsys.readouterr()
        assert "已存在" in captured.out
        assert "跳过" in captured.out

    async def test_existing_user_does_not_create_second(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """幂等：不创建第二个同名管理员。"""
        settings = _make_mock_settings()
        provider = _make_mock_provider()

        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyUserUnitOfWork") as mock_uow_cls,
            patch("app.cli.auth.SqlAlchemyUserRepository") as mock_repo_cls,
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value="P@ssw0rd1234"),
        ):
            mock_uow_cls.return_value = mock_uow
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_username = AsyncMock(return_value=MagicMock())
            mock_repo.add = AsyncMock()

            await _async_create_admin(settings)

            # add 不应被调用（用户已存在，跳过创建）
            mock_repo.add.assert_not_called()


class TestCreateAdminPasswordSafety:
    """create-admin 密码安全——不在输出/日志中。"""

    async def test_password_not_in_output(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """密码不出现在命令输出中。"""
        settings = _make_mock_settings()
        provider = _make_mock_provider()
        password = "MySecretP@ssw0rd123"

        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyUserUnitOfWork") as mock_uow_cls,
            patch("app.cli.auth.SqlAlchemyUserRepository") as mock_repo_cls,
            patch("app.cli.auth.SqlAlchemyRbacUnitOfWork") as mock_rbac_uow_cls,
            patch(
                "app.modules.rbac.infrastructure.repository.SqlAlchemyRoleRepository"
            ) as mock_role_repo_cls,
            patch(
                "app.modules.rbac.infrastructure.repository.SqlAlchemyUserRoleRepository"
            ) as mock_ur_repo_cls,
            patch("app.cli.auth.PasswordHasher") as mock_hasher_cls,
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value=password),
            patch("app.modules.user.domain.model.User") as mock_user_cls,
        ):
            mock_uow_cls.return_value = mock_uow
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_username = AsyncMock(return_value=None)

            # Rbac UoW for role creation
            mock_rbac_uow = AsyncMock()
            mock_rbac_uow.__aenter__ = AsyncMock(return_value=mock_rbac_uow)
            mock_rbac_uow.__aexit__ = AsyncMock(return_value=None)
            mock_rbac_uow_cls.return_value = mock_rbac_uow

            mock_role_repo = mock_role_repo_cls.return_value
            existing_role = MagicMock()
            existing_role.id = "role-uuid"
            mock_role_repo.get_by_code = AsyncMock(return_value=existing_role)

            mock_ur_repo = mock_ur_repo_cls.return_value
            mock_ur_repo.assign = AsyncMock()

            mock_hasher = mock_hasher_cls.return_value
            mock_hasher.hash.return_value = "hashed_value"

            mock_user = MagicMock()
            mock_user.id = "user-uuid"
            mock_user_cls.new.return_value = mock_user
            mock_repo.add = AsyncMock()

            result = await _async_create_admin(settings)

        assert result == 0
        captured = capsys.readouterr()
        assert password not in captured.out
        assert password not in captured.err

    async def test_password_not_in_logs(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """密码不出现在日志中。"""
        import logging

        settings = _make_mock_settings()
        provider = _make_mock_provider()
        password = "MySecretP@ssw0rd456"

        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyUserUnitOfWork") as mock_uow_cls,
            patch("app.cli.auth.SqlAlchemyUserRepository") as mock_repo_cls,
            patch("app.cli.auth.SqlAlchemyRbacUnitOfWork") as mock_rbac_uow_cls,
            patch(
                "app.modules.rbac.infrastructure.repository.SqlAlchemyRoleRepository"
            ) as mock_role_repo_cls,
            patch(
                "app.modules.rbac.infrastructure.repository.SqlAlchemyUserRoleRepository"
            ) as mock_ur_repo_cls,
            patch("app.cli.auth.PasswordHasher") as mock_hasher_cls,
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value=password),
            patch("app.modules.user.domain.model.User") as mock_user_cls,
        ):
            mock_uow_cls.return_value = mock_uow
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_username = AsyncMock(return_value=None)

            mock_rbac_uow = AsyncMock()
            mock_rbac_uow.__aenter__ = AsyncMock(return_value=mock_rbac_uow)
            mock_rbac_uow.__aexit__ = AsyncMock(return_value=None)
            mock_rbac_uow_cls.return_value = mock_rbac_uow

            mock_role_repo = mock_role_repo_cls.return_value
            existing_role = MagicMock()
            existing_role.id = "role-uuid"
            mock_role_repo.get_by_code = AsyncMock(return_value=existing_role)

            mock_ur_repo = mock_ur_repo_cls.return_value
            mock_ur_repo.assign = AsyncMock()

            mock_hasher = mock_hasher_cls.return_value
            mock_hasher.hash.return_value = "hashed_value"

            mock_user = MagicMock()
            mock_user.id = "user-uuid"
            mock_user_cls.new.return_value = mock_user
            mock_repo.add = AsyncMock()

            with caplog.at_level(logging.INFO, logger="app.cli.auth"):
                await _async_create_admin(settings)

        for record in caplog.records:
            assert password not in record.getMessage()
            assert password not in str(record.__dict__)


class TestCreateAdminDisposesProvider:
    """create-admin 在 finally 中释放连接池。"""

    async def test_dispose_called_on_success(self) -> None:
        """成功时释放连接池。"""
        settings = _make_mock_settings()
        provider = _make_mock_provider()
        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyUserUnitOfWork") as mock_uow_cls,
            patch("app.cli.auth.SqlAlchemyUserRepository") as mock_repo_cls,
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value="P@ssw0rd1234"),
        ):
            mock_uow_cls.return_value = mock_uow
            mock_repo = mock_repo_cls.return_value
            # 用户已存在 → 幂等返回，走 provider 创建和 finally 释放路径
            mock_repo.get_by_username = AsyncMock(return_value=MagicMock())

            await _async_create_admin(settings)

        provider.dispose.assert_called_once()

    async def test_dispose_called_on_exception(self) -> None:
        """异常时也释放连接池。"""
        settings = _make_mock_settings()
        provider = _make_mock_provider()
        provider.initialize = AsyncMock(side_effect=RuntimeError("连接失败"))

        with (
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth._read_input", side_effect=["admin", "Admin"]),
            patch("app.cli.auth._read_password", return_value="P@ssw0rd1234"),
            pytest.raises(RuntimeError, match="连接失败"),
        ):
            await _async_create_admin(settings)

        provider.dispose.assert_called_once()


class TestCreateAdminEntry:
    """auth_create_admin 入口函数。"""

    @patch("app.cli.auth.asyncio.run")
    @patch("app.cli.auth.Settings")
    def test_calls_async_create_admin(
        self,
        mock_settings_cls: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """auth_create_admin 调用异步实现。"""
        mock_settings_cls.return_value = MagicMock()
        mock_run.return_value = 0

        result = auth_create_admin()

        assert result == 0
        mock_run.assert_called_once()


# ===========================================================================
# auth sync-permissions
# ===========================================================================


class TestSyncPermissions:
    """sync-permissions CLI 命令。"""

    async def test_outputs_sync_summary(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """输出同步结果摘要。"""
        from app.modules.rbac.application.permission_sync import SyncResult

        settings = _make_mock_settings()
        provider = _make_mock_provider()

        mock_result = SyncResult(
            added=frozenset({"a:b:c"}),
            updated=frozenset(),
            unchanged=frozenset({"d:e:f"}),
            orphans=frozenset(),
            orphan_referenced=frozenset(),
        )

        with (
            patch("app.cli.auth.get_enabled_modules", return_value=[]),
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyRbacUnitOfWork"),
            patch("app.cli.auth.PermissionSyncService") as mock_service_cls,
        ):
            mock_service = mock_service_cls.return_value
            mock_service.sync = AsyncMock(return_value=mock_result)

            result = await _async_sync_permissions(settings)

        assert result == 0
        captured = capsys.readouterr()
        assert "权限同步完成" in captured.out
        assert "新增 1" in captured.out
        assert "未变 1" in captured.out

    async def test_reports_orphans(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """输出孤立权限点报告。"""
        from app.modules.rbac.application.permission_sync import SyncResult

        settings = _make_mock_settings()
        provider = _make_mock_provider()

        mock_result = SyncResult(
            added=frozenset(),
            updated=frozenset(),
            unchanged=frozenset(),
            orphans=frozenset({"system:old:perm"}),
            orphan_referenced=frozenset(),
        )

        with (
            patch("app.cli.auth.get_enabled_modules", return_value=[]),
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyRbacUnitOfWork"),
            patch("app.cli.auth.PermissionSyncService") as mock_service_cls,
        ):
            mock_service = mock_service_cls.return_value
            mock_service.sync = AsyncMock(return_value=mock_result)

            result = await _async_sync_permissions(settings)

        assert result == 0
        captured = capsys.readouterr()
        assert "孤立权限点" in captured.out
        assert "system:old:perm" in captured.out
        assert "未自动删除" in captured.out

    async def test_reports_orphan_referenced_by_role(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """被角色引用的孤立权限点标记 [仍被角色引用]。"""
        from app.modules.rbac.application.permission_sync import SyncResult

        settings = _make_mock_settings()
        provider = _make_mock_provider()

        mock_result = SyncResult(
            added=frozenset(),
            updated=frozenset(),
            unchanged=frozenset(),
            orphans=frozenset({"system:old:perm"}),
            orphan_referenced=frozenset({"system:old:perm"}),
        )

        with (
            patch("app.cli.auth.get_enabled_modules", return_value=[]),
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyRbacUnitOfWork"),
            patch("app.cli.auth.PermissionSyncService") as mock_service_cls,
        ):
            mock_service = mock_service_cls.return_value
            mock_service.sync = AsyncMock(return_value=mock_result)

            await _async_sync_permissions(settings)

        captured = capsys.readouterr()
        assert "仍被角色引用" in captured.out

    async def test_disposes_provider(self) -> None:
        """sync-permissions 在 finally 中释放连接池。"""
        settings = _make_mock_settings()
        provider = _make_mock_provider()

        from app.modules.rbac.application.permission_sync import SyncResult

        mock_result = SyncResult()

        with (
            patch("app.cli.auth.get_enabled_modules", return_value=[]),
            patch("app.cli.auth.SqlAlchemyDbPoolProvider", return_value=provider),
            patch("app.cli.auth.SqlAlchemyRbacUnitOfWork"),
            patch("app.cli.auth.PermissionSyncService") as mock_service_cls,
        ):
            mock_service = mock_service_cls.return_value
            mock_service.sync = AsyncMock(return_value=mock_result)

            await _async_sync_permissions(settings)

        provider.dispose.assert_called_once()


class TestSyncPermissionsEntry:
    """auth_sync_permissions 入口函数。"""

    @patch("app.cli.auth.asyncio.run")
    @patch("app.cli.auth.Settings")
    def test_calls_async_sync(
        self,
        mock_settings_cls: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """auth_sync_permissions 调用异步实现。"""
        mock_settings_cls.return_value = MagicMock()
        mock_run.return_value = 0

        result = auth_sync_permissions()

        assert result == 0
        mock_run.assert_called_once()
