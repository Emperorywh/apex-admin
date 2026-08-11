"""G2 安全模块分支覆盖率补充测试 — SPEC 28.3 / 34.2.

覆盖 identity/auth/rbac use case 中的错误路径与边界分支，
使四模块语句与分支覆盖率均达到 90% 门槛。

使用 AsyncMock 隔离数据库依赖，聚焦于 Use Case 内部分支逻辑。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.application.context import UseCaseContext
from app.application.ports import Clock, IdGenerator
from app.core.security.password import Argon2Hasher

if TYPE_CHECKING:
    from app.modules.auth.use_case import AuthUseCase
from app.modules.identity.models import User, UserStatus
from app.modules.identity.schemas import (
    SelfProfileUpdateRequest,
    UserUpdateRequest,
)
from app.modules.identity.use_case import UserUseCase
from app.modules.rbac.models import Role, RoleStatus
from app.modules.rbac.schemas import (
    AssignPermissionsRequest,
    AssignUserRolesRequest,
    RoleUpdateRequest,
)
from app.modules.rbac.use_case import RbacUseCase

# ── 固定测试常量 ───────────────────────────────────────────────────────────


_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_USER_ID = uuid4()
_ACTOR_ID = str(uuid4())


class _FixedClock(Clock):
    """固定时钟。"""

    def now(self) -> datetime:
        return _NOW


class _FixedIdGen(IdGenerator):
    """固定 ID 生成器。"""

    def __init__(self, *ids: UUID) -> None:
        self._ids = list(ids) or [uuid4()]
        self._n = 0

    def generate_id(self) -> UUID:
        if self._n < len(self._ids):
            r = self._ids[self._n]
            self._n += 1
            return r
        return uuid4()


def _make_user(
    *,
    status: UserStatus = UserStatus.ACTIVE,
    user_id: UUID = _USER_ID,
) -> User:
    """构造测试用户实体。"""

    return User(
        id=user_id,
        username="testuser",
        display_name="Test User",
        password_hash="fake_hash",
        status=status,
        phone=None,
        email=None,
        last_login_at=None,
        password_updated_at=None,
        created_at=_NOW,
        updated_at=_NOW,
        created_by=None,
        updated_by=None,
    )


def _make_ctx(actor_id: str | None = _ACTOR_ID) -> UseCaseContext:
    """构造 UseCaseContext。"""

    return UseCaseContext(
        request_id="test-req",
        actor_id=actor_id,
        current_time=_NOW,
        security_metadata=MappingProxyType({}),
    )


def _make_role(
    *,
    is_builtin: bool = False,
    status: RoleStatus = RoleStatus.ACTIVE,
    code: str = "editor",
) -> Role:
    """构造测试角色实体。"""

    return Role(
        id=uuid4(),
        code=code,
        display_name="编辑者",
        description="",
        status=status,
        is_builtin=is_builtin,
        sort_order=0,
        created_at=_NOW,
        updated_at=_NOW,
        created_by=None,
        updated_by=None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Identity Use Case 分支覆盖
# ═══════════════════════════════════════════════════════════════════════════════


def _make_identity_use_case(
    *,
    repo_get_by_id_return: User | None = None,
    audit_count: int = 0,
    actor_permissions: frozenset[str] | None = None,
    actor_is_super: bool = True,
    target_permissions: frozenset[str] | None = None,
    active_count: int = 0,
    role_codes: set[str] | None = None,
) -> tuple[UserUseCase, dict[str, AsyncMock]]:
    """构造带 mock 依赖的 UserUseCase。"""

    mock_uow = MagicMock()
    mock_session = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=None)
    mock_uow.commit = AsyncMock()
    mock_uow.rollback = AsyncMock()

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=repo_get_by_id_return)
    mock_repo.save = AsyncMock()
    mock_repo.delete_by_id = AsyncMock()
    mock_repo.list_users = AsyncMock(return_value=([], 0))

    mock_audit = AsyncMock()
    mock_audit.record_audit = AsyncMock()
    mock_audit.count_by_resource = AsyncMock(return_value=audit_count)

    mock_rbac_port = AsyncMock()
    mock_rbac_port.get_effective_permission_codes = AsyncMock(
        return_value=target_permissions or frozenset(),
    )
    mock_rbac_port.get_role_codes_by_user = AsyncMock(
        return_value=role_codes or set(),
    )
    mock_rbac_port.get_user_ids_by_role_code = AsyncMock(return_value=set())

    mock_auth_port = AsyncMock()
    mock_auth_port.count_active_users_by_ids = AsyncMock(return_value=active_count)

    uow_factory = MagicMock(return_value=mock_uow)

    uc = UserUseCase(
        uow_factory=uow_factory,
        clock=_FixedClock(),
        id_generator=_FixedIdGen(),
        hasher=Argon2Hasher(),
        event_handlers=[],
        audit_factory=lambda session: mock_audit,
        user_rbac_port_factory=lambda session: mock_rbac_port,
        user_auth_port_factory=lambda session: mock_auth_port,
    )

    # Patch _create_repo to return mock
    patch.object(uc, "_create_repo", return_value=mock_repo).start()

    # Patch _verify_actor_authorization for management scope checks
    if actor_permissions is not None:
        patch.object(
            uc,
            "_verify_actor_authorization",
            AsyncMock(return_value=(actor_permissions, actor_is_super)),
        ).start()

    return uc, {
        "uow": mock_uow,
        "repo": mock_repo,
        "audit": mock_audit,
        "rbac_port": mock_rbac_port,
        "auth_port": mock_auth_port,
    }


@pytest.mark.g2
@pytest.mark.unit
class TestIdentityUseCaseBranches:
    """UserUseCase 错误路径与边界分支。"""

    async def test_get_user_success(self) -> None:
        """get_user 成功返回 — 覆盖 user-is-not-None 分支。"""

        uc, _ = _make_identity_use_case(repo_get_by_id_return=_make_user())
        result = await uc.get_user(_make_ctx(), _USER_ID)
        assert result.username == "testuser"

    async def test_get_user_not_found(self) -> None:
        """get_user 用户不存在 — 覆盖 user-is-None 分支。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            await uc.get_user(_make_ctx(), _USER_ID)

    async def test_list_users(self) -> None:
        """list_users 返回分页结构。"""

        uc, _ = _make_identity_use_case()
        result = await uc.list_users(
            _make_ctx(),
            page=1,
            page_size=10,
            sort_fields=[],
        )
        assert "items" in result
        assert result["total"] == 0

    async def test_update_user_not_found(self) -> None:
        """update_user 用户不存在 — 覆盖 existing-is-None 分支。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            await uc.update_user(
                _make_ctx(),
                _USER_ID,
                UserUpdateRequest(display_name="New"),
            )

    async def test_update_user_success(self) -> None:
        """update_user 成功 — 覆盖 existing-is-not-None 分支。"""

        uc, _ = _make_identity_use_case(
            repo_get_by_id_return=_make_user(),
            actor_permissions=frozenset(),
        )
        result = await uc.update_user(
            _make_ctx(),
            _USER_ID,
            UserUpdateRequest(display_name="Updated"),
        )
        assert result.display_name == "Updated"

    async def test_enable_user_not_found(self) -> None:
        """enable_user 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            await uc.enable_user(_make_ctx(), _USER_ID)

    async def test_enable_user_already_active(self) -> None:
        """enable_user 已启用 — 覆盖 status==ACTIVE 分支。"""

        from app.modules.identity.errors import UserAlreadyActiveError

        uc, _ = _make_identity_use_case(
            repo_get_by_id_return=_make_user(status=UserStatus.ACTIVE),
        )
        with pytest.raises(UserAlreadyActiveError):
            await uc.enable_user(_make_ctx(), _USER_ID)

    async def test_enable_user_success(self) -> None:
        """enable_user 从禁用启用 — 覆盖 status!=ACTIVE 分支。"""

        uc, _ = _make_identity_use_case(
            repo_get_by_id_return=_make_user(status=UserStatus.DISABLED),
            actor_permissions=frozenset(),
        )
        result = await uc.enable_user(_make_ctx(), _USER_ID)
        assert result.status == "active"

    async def test_disable_user_not_found(self) -> None:
        """disable_user 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            await uc.disable_user(_make_ctx(), _USER_ID)

    async def test_reset_password_not_found(self) -> None:
        """reset_password 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            from app.modules.identity.schemas import UserResetPasswordRequest

            await uc.reset_password(
                _make_ctx(),
                _USER_ID,
                UserResetPasswordRequest(new_password="new_secure_pass_12"),
            )

    async def test_delete_user_not_found(self) -> None:
        """delete_user 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            await uc.delete_user(_make_ctx(), _USER_ID)

    async def test_delete_user_success(self) -> None:
        """delete_user 无审计记录成功。"""

        uc, _ = _make_identity_use_case(
            repo_get_by_id_return=_make_user(),
            audit_count=0,
            actor_permissions=frozenset(),
            role_codes=set(),
        )
        await uc.delete_user(_make_ctx(), _USER_ID)

    async def test_get_self_profile_success(self) -> None:
        """get_self_profile 成功。"""

        uc, _ = _make_identity_use_case(repo_get_by_id_return=_make_user())
        result = await uc.get_self_profile(_make_ctx())
        assert result.username == "testuser"

    async def test_get_self_profile_not_found(self) -> None:
        """get_self_profile 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            await uc.get_self_profile(_make_ctx())

    async def test_update_self_profile_success(self) -> None:
        """update_self_profile 成功。"""

        uc, _ = _make_identity_use_case(repo_get_by_id_return=_make_user())
        result = await uc.update_self_profile(
            _make_ctx(),
            SelfProfileUpdateRequest(display_name="Self Updated"),
        )
        assert result.display_name == "Self Updated"

    async def test_update_self_profile_not_found(self) -> None:
        """update_self_profile 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_identity_use_case(repo_get_by_id_return=None)
        with pytest.raises(UserNotFoundError):
            await uc.update_self_profile(
                _make_ctx(),
                SelfProfileUpdateRequest(display_name="X"),
            )

    async def test_change_self_password_success(self) -> None:
        """change_self_password 正确旧密码。"""

        # 需要真实哈希器验证旧密码
        hasher = Argon2Hasher()
        real_hash = hasher.hash("old_secure_pass_12")
        user = _make_user()
        user_with_hash = User(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            password_hash=real_hash,
            status=user.status,
            phone=user.phone,
            email=user.email,
            last_login_at=user.last_login_at,
            password_updated_at=user.password_updated_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
            created_by=user.created_by,
            updated_by=user.updated_by,
        )

        uc, _ = _make_identity_use_case(
            repo_get_by_id_return=user_with_hash,
        )
        uc._hasher = hasher  # 使用真实哈希器

        from app.modules.identity.schemas import SelfChangePasswordRequest

        await uc.change_self_password(
            _make_ctx(),
            SelfChangePasswordRequest(
                old_password="old_secure_pass_12",
                new_password="new_secure_pass_12",
            ),
        )

    async def test_change_self_password_wrong_old(self) -> None:
        """change_self_password 错误旧密码。"""

        hasher = Argon2Hasher()
        real_hash = hasher.hash("correct_secure_pass_12")
        user = User(
            id=_USER_ID,
            username="testuser",
            display_name="Test User",
            password_hash=real_hash,
            status=UserStatus.ACTIVE,
            phone=None,
            email=None,
            last_login_at=None,
            password_updated_at=None,
            created_at=_NOW,
            updated_at=_NOW,
            created_by=None,
            updated_by=None,
        )

        uc, _ = _make_identity_use_case(repo_get_by_id_return=user)

        from app.modules.identity.errors import UserInvalidOldPasswordError
        from app.modules.identity.schemas import SelfChangePasswordRequest

        with pytest.raises(UserInvalidOldPasswordError):
            await uc.change_self_password(
                _make_ctx(),
                SelfChangePasswordRequest(
                    old_password="wrong_password_12",
                    new_password="new_secure_pass_12",
                ),
            )

    async def test_verify_actor_authorization_none(self) -> None:
        """_verify_actor_authorization actor_id=None 返回空集。"""

        # actor_permissions=None 时不 patch _verify_actor_authorization
        uc, _ = _make_identity_use_case(actor_permissions=None)
        result = await uc._verify_actor_authorization(
            MagicMock(),
            None,
        )
        assert result == (frozenset(), False)

    async def test_verify_actor_authorization_invalid_uuid(self) -> None:
        """_verify_actor_authorization 无效 UUID 返回空集。"""

        uc, _ = _make_identity_use_case(actor_permissions=None)
        result = await uc._verify_actor_authorization(
            MagicMock(),
            "not-a-uuid",
        )
        assert result == (frozenset(), False)


# ═══════════════════════════════════════════════════════════════════════════════
# RBAC Use Case 分支覆盖
# ═══════════════════════════════════════════════════════════════════════════════


def _make_rbac_use_case(
    *,
    repo_role: Role | None = None,
    repo_roles_by_codes: list[Role] | None = None,
    member_count: int = 0,
    audit_count: int = 0,
    permission_codes: list | None = None,
    role_permission_codes: list[str] | None = None,
    user_exists: bool = True,
    actor_permissions: frozenset[str] | None = None,
    actor_is_super: bool = True,
    user_role_list: list | None = None,
) -> tuple[RbacUseCase, dict[str, AsyncMock]]:
    """构造带 mock 依赖的 RbacUseCase。"""

    mock_uow = MagicMock()
    mock_session = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=None)
    mock_uow.commit = AsyncMock()
    mock_uow.rollback = AsyncMock()

    mock_repo = AsyncMock()
    mock_repo.get_role_by_id = AsyncMock(return_value=repo_role)
    mock_repo.get_roles_by_codes = AsyncMock(return_value=repo_roles_by_codes or [])
    mock_repo.get_roles_by_ids = AsyncMock(return_value=[])
    mock_repo.save_role = AsyncMock()
    mock_repo.delete_role_by_id = AsyncMock(return_value=True)
    mock_repo.count_role_members = AsyncMock(return_value=member_count)
    mock_repo.list_roles = AsyncMock(return_value=([], 0))
    mock_repo.get_permission_codes = AsyncMock(return_value=permission_codes or [])
    mock_repo.replace_role_permissions = AsyncMock()
    mock_repo.get_role_permission_codes = AsyncMock(
        return_value=role_permission_codes or [],
    )
    mock_repo.add_user_role = AsyncMock()
    mock_repo.remove_user_role = AsyncMock(return_value=True)
    mock_repo.list_user_roles = AsyncMock(return_value=user_role_list or [])
    mock_repo.list_role_members = AsyncMock(return_value=([], 0))
    mock_repo.count_roles_for_user = AsyncMock(return_value=0)

    mock_audit = AsyncMock()
    mock_audit.record_audit = AsyncMock()

    mock_rbac_port = AsyncMock()
    mock_rbac_port.get_effective_permission_codes = AsyncMock(return_value=frozenset())
    mock_rbac_port.get_role_codes_by_user = AsyncMock(return_value=set())
    mock_rbac_port.get_role_ids_by_user = AsyncMock(return_value=[])
    mock_rbac_port.get_user_ids_by_role_code = AsyncMock(return_value=set())

    mock_auth_port = AsyncMock()
    mock_auth_port.get_status_by_id = AsyncMock(
        return_value=UserStatus.ACTIVE if user_exists else None,
    )
    mock_auth_port.count_active_users_by_ids = AsyncMock(return_value=1)

    uow_factory = MagicMock(return_value=mock_uow)

    uc = RbacUseCase(
        uow_factory=uow_factory,
        clock=_FixedClock(),
        id_generator=_FixedIdGen(),
        audit_factory=lambda session: mock_audit,
        user_auth_port_factory=lambda session: mock_auth_port,
        user_rbac_port_factory=lambda session: mock_rbac_port,
    )

    patch.object(uc, "_create_repo", return_value=mock_repo).start()

    if actor_permissions is not None:
        patch.object(
            uc,
            "_verify_actor_authorization",
            AsyncMock(return_value=(actor_permissions, actor_is_super)),
        ).start()

    return uc, {
        "uow": mock_uow,
        "repo": mock_repo,
        "audit": mock_audit,
    }


@pytest.mark.g2
@pytest.mark.unit
class TestRbacUseCaseBranches:
    """RbacUseCase 错误路径与边界分支。"""

    async def test_get_role_detail_not_found(self) -> None:
        """get_role_detail 角色不存在。"""

        from app.modules.rbac.errors import RoleNotFoundError

        uc, _ = _make_rbac_use_case(repo_role=None)
        with pytest.raises(RoleNotFoundError):
            await uc.get_role_detail(_make_ctx(), uuid4())

    async def test_get_role_detail_success(self) -> None:
        """get_role_detail 成功。"""

        uc, _ = _make_rbac_use_case(repo_role=_make_role())
        result = await uc.get_role_detail(_make_ctx(), uuid4())
        assert result.code == "editor"

    async def test_update_role_not_found(self) -> None:
        """update_role 角色不存在。"""

        from app.modules.rbac.errors import RoleNotFoundError

        uc, _ = _make_rbac_use_case(repo_role=None)
        with pytest.raises(RoleNotFoundError):
            await uc.update_role(
                _make_ctx(),
                uuid4(),
                RoleUpdateRequest(display_name="New"),
            )

    async def test_update_role_success(self) -> None:
        """update_role 成功。"""

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(),
            actor_permissions=frozenset(),
        )
        result = await uc.update_role(
            _make_ctx(),
            uuid4(),
            RoleUpdateRequest(display_name="New Name"),
        )
        assert result.display_name == "New Name"

    async def test_enable_role_builtin_protected(self) -> None:
        """enable_role 内置角色保护。"""

        from app.modules.rbac.errors import BuiltinRoleProtectedError

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(is_builtin=True, status=RoleStatus.DISABLED),
        )
        with pytest.raises(BuiltinRoleProtectedError):
            await uc.enable_role(_make_ctx(), uuid4())

    async def test_enable_role_already_active(self) -> None:
        """enable_role 已启用。"""

        from app.modules.rbac.errors import RoleAlreadyActiveError

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(status=RoleStatus.ACTIVE),
        )
        with pytest.raises(RoleAlreadyActiveError):
            await uc.enable_role(_make_ctx(), uuid4())

    async def test_enable_role_success(self) -> None:
        """enable_role 从禁用启用。"""

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(status=RoleStatus.DISABLED),
            actor_permissions=frozenset(),
        )
        result = await uc.enable_role(_make_ctx(), uuid4())
        assert result.status == "active"

    async def test_disable_role_builtin_protected(self) -> None:
        """disable_role 内置角色保护。"""

        from app.modules.rbac.errors import BuiltinRoleProtectedError

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(is_builtin=True),
        )
        with pytest.raises(BuiltinRoleProtectedError):
            await uc.disable_role(_make_ctx(), uuid4())

    async def test_disable_role_already_disabled(self) -> None:
        """disable_role 已禁用。"""

        from app.modules.rbac.errors import RoleAlreadyDisabledError

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(status=RoleStatus.DISABLED),
        )
        with pytest.raises(RoleAlreadyDisabledError):
            await uc.disable_role(_make_ctx(), uuid4())

    async def test_disable_role_success(self) -> None:
        """disable_role 成功。"""

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(status=RoleStatus.ACTIVE),
            actor_permissions=frozenset(),
        )
        result = await uc.disable_role(_make_ctx(), uuid4())
        assert result.status == "disabled"

    async def test_delete_role_not_found(self) -> None:
        """delete_role 角色不存在。"""

        from app.modules.rbac.errors import RoleNotFoundError

        uc, _ = _make_rbac_use_case(repo_role=None)
        with pytest.raises(RoleNotFoundError):
            await uc.delete_role(_make_ctx(), uuid4())

    async def test_delete_role_builtin_protected(self) -> None:
        """delete_role 内置角色保护。"""

        from app.modules.rbac.errors import BuiltinRoleProtectedError

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(is_builtin=True),
        )
        with pytest.raises(BuiltinRoleProtectedError):
            await uc.delete_role(_make_ctx(), uuid4())

    async def test_delete_role_has_users(self) -> None:
        """delete_role 有用户关联。"""

        from app.modules.rbac.errors import RoleHasUsersError

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(),
            member_count=5,
        )
        with pytest.raises(RoleHasUsersError):
            await uc.delete_role(_make_ctx(), uuid4())

    async def test_delete_role_success(self) -> None:
        """delete_role 成功。"""

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(),
            member_count=0,
            actor_permissions=frozenset(),
        )
        await uc.delete_role(_make_ctx(), uuid4())

    async def test_assign_permissions_not_found(self) -> None:
        """assign_permissions 角色不存在。"""

        from app.modules.rbac.errors import RoleNotFoundError

        uc, _ = _make_rbac_use_case(repo_role=None)
        with pytest.raises(RoleNotFoundError):
            await uc.assign_permissions(
                _make_ctx(),
                uuid4(),
                AssignPermissionsRequest(permission_codes=[]),
            )

    async def test_assign_permissions_success(self) -> None:
        """assign_permissions 成功（空权限集）。"""

        uc, _ = _make_rbac_use_case(
            repo_role=_make_role(),
            actor_permissions=frozenset(),
        )
        result = await uc.assign_permissions(
            _make_ctx(),
            uuid4(),
            AssignPermissionsRequest(permission_codes=[]),
        )
        assert result is not None

    async def test_assign_user_roles_user_not_found(self) -> None:
        """assign_user_roles 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_rbac_use_case(user_exists=False)
        with pytest.raises(UserNotFoundError):
            await uc.assign_user_roles(
                _make_ctx(),
                uuid4(),
                AssignUserRolesRequest(role_codes=[]),
            )

    async def test_assign_user_roles_success(self) -> None:
        """assign_user_roles 成功（空角色集）。"""

        uc, _ = _make_rbac_use_case(
            user_exists=True,
            actor_permissions=frozenset(),
        )
        result = await uc.assign_user_roles(
            _make_ctx(),
            uuid4(),
            AssignUserRolesRequest(role_codes=[]),
        )
        assert result["user_id"] is not None

    async def test_remove_user_role_not_assigned(self) -> None:
        """remove_user_role 未分配。"""

        from app.modules.rbac.errors import UserRoleNotAssignedError

        uc, mocks = _make_rbac_use_case(
            user_exists=True,
            actor_permissions=frozenset(),
        )
        mocks["repo"].remove_user_role = AsyncMock(return_value=False)
        with pytest.raises(UserRoleNotAssignedError):
            await uc.remove_user_role(_make_ctx(), uuid4(), uuid4())

    async def test_remove_user_role_success(self) -> None:
        """remove_user_role 成功。"""

        uc, mocks = _make_rbac_use_case(
            user_exists=True,
            actor_permissions=frozenset(),
        )
        mocks["repo"].remove_user_role = AsyncMock(return_value=True)
        await uc.remove_user_role(_make_ctx(), uuid4(), uuid4())

    async def test_get_user_roles_not_found(self) -> None:
        """get_user_roles 用户不存在。"""

        from app.modules.identity.errors import UserNotFoundError

        uc, _ = _make_rbac_use_case(user_exists=False)
        with pytest.raises(UserNotFoundError):
            await uc.get_user_roles(_make_ctx(), uuid4())

    async def test_get_user_roles_success(self) -> None:
        """get_user_roles 成功。"""

        uc, _ = _make_rbac_use_case(user_exists=True)
        result = await uc.get_user_roles(_make_ctx(), uuid4())
        assert result["role_ids"] == []

    async def test_list_roles_success(self) -> None:
        """list_roles 成功。"""

        uc, _ = _make_rbac_use_case()
        result = await uc.list_roles(
            _make_ctx(),
            page=1,
            page_size=10,
            sort_fields=[],
        )
        assert result["total"] == 0

    async def test_verify_actor_authorization_none(self) -> None:
        """_verify_actor_authorization actor_id=None。"""

        patch.stopall()
        uc, _ = _make_rbac_use_case()
        result = await uc._verify_actor_authorization(MagicMock(), None)
        assert result == (frozenset(), False)

    async def test_verify_actor_authorization_invalid_uuid(self) -> None:
        """_verify_actor_authorization 无效 UUID。"""

        patch.stopall()
        uc, _ = _make_rbac_use_case()
        result = await uc._verify_actor_authorization(MagicMock(), "bad-uuid")
        assert result == (frozenset(), False)

    async def test_check_last_super_admin_not_super(self) -> None:
        """_check_last_super_admin_protection 非超管不触发保护。"""

        uc, _ = _make_rbac_use_case()

        # Mock the ports directly on the use case
        mock_rbac_port = AsyncMock()
        mock_rbac_port.get_role_codes_by_user = AsyncMock(return_value=set())
        mock_rbac_port.get_user_ids_by_role_code = AsyncMock(return_value=set())
        mock_auth_port = AsyncMock()
        mock_auth_port.count_active_users_by_ids = AsyncMock(return_value=0)
        patch.object(uc, "_create_user_rbac_port", return_value=mock_rbac_port).start()
        patch.object(uc, "_create_user_auth_port", return_value=mock_auth_port).start()

        # 不应抛异常
        await uc._check_last_super_admin_protection(MagicMock(), uuid4())

    async def test_check_last_super_admin_last_one(self) -> None:
        """_check_last_super_admin_protection 最后一个超管触发保护。"""

        from app.core.security.authorization import SUPER_ADMIN_ROLE_CODE
        from app.modules.auth.errors import LastSuperAdminError

        uc, _ = _make_rbac_use_case()

        mock_rbac_port = AsyncMock()
        mock_rbac_port.get_role_codes_by_user = AsyncMock(
            return_value={SUPER_ADMIN_ROLE_CODE},
        )
        mock_rbac_port.get_user_ids_by_role_code = AsyncMock(return_value={uuid4()})
        mock_auth_port = AsyncMock()
        mock_auth_port.count_active_users_by_ids = AsyncMock(return_value=1)
        patch.object(uc, "_create_user_rbac_port", return_value=mock_rbac_port).start()
        patch.object(uc, "_create_user_auth_port", return_value=mock_auth_port).start()

        with pytest.raises(LastSuperAdminError):
            await uc._check_last_super_admin_protection(MagicMock(), uuid4())


# ═══════════════════════════════════════════════════════════════════════════════
# Auth Dependencies 分支覆盖
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAuthDependencyBranches:
    """auth/dependencies.py 分支覆盖。"""

    def test_extract_bearer_token_none(self) -> None:
        """Authorization 头缺失。"""

        from app.core.errors.exceptions import AuthenticationError
        from app.modules.auth.dependencies import extract_bearer_token

        with pytest.raises(AuthenticationError):
            extract_bearer_token(None)

    def test_extract_bearer_token_wrong_prefix(self) -> None:
        """Authorization 头格式不合法。"""

        from app.core.errors.exceptions import AuthenticationError
        from app.modules.auth.dependencies import extract_bearer_token

        with pytest.raises(AuthenticationError):
            extract_bearer_token("Basic abc123")

    def test_extract_bearer_token_empty(self) -> None:
        """Bearer 后空 Token。"""

        from app.core.errors.exceptions import AuthenticationError
        from app.modules.auth.dependencies import extract_bearer_token

        with pytest.raises(AuthenticationError):
            extract_bearer_token("Bearer ")

    def test_extract_bearer_token_valid(self) -> None:
        """有效 Bearer Token。"""

        from app.modules.auth.dependencies import extract_bearer_token

        token = extract_bearer_token("Bearer mytoken123")
        assert token == "mytoken123"

    async def test_get_auth_use_case_unavailable(self) -> None:
        """app.state 无 auth_use_case 时抛 AuthenticationError。"""

        from app.core.errors.exceptions import AuthenticationError
        from app.modules.auth.dependencies import _get_auth_use_case

        request = MagicMock()
        request.app.state = MagicMock(spec=[])  # 无 auth_use_case 属性

        with pytest.raises(AuthenticationError):
            _get_auth_use_case(request)


# ═══════════════════════════════════════════════════════════════════════════════
# Auth Handlers 分支覆盖
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAuthHandlerBranches:
    """auth/handlers.py 分支覆盖。"""

    async def test_disabled_handler_empty_user_id(self) -> None:
        """USER.DISABLED 处理器空 user_id 早返回 — 覆盖 early-return 分支。"""

        from app.core.events.events import DomainEvent
        from app.modules.auth.handlers import RevokeSessionsOnUserDisabled

        handler = RevokeSessionsOnUserDisabled()
        event = DomainEvent(
            code="USER.DISABLED",
            payload={"user_id": ""},
        )
        mock_session = MagicMock()
        # 空 user_id → 早返回，不执行数据库操作
        await handler.handle(event, mock_session)
        mock_session.execute.assert_not_called()

    async def test_password_reset_handler_empty_user_id(self) -> None:
        """USER.PASSWORD_RESET_BY_ADMIN 处理器空 user_id 早返回。"""

        from app.core.events.events import DomainEvent
        from app.modules.auth.handlers import RevokeSessionsOnPasswordReset

        handler = RevokeSessionsOnPasswordReset()
        event = DomainEvent(
            code="USER.PASSWORD_RESET_BY_ADMIN",
            payload={"user_id": ""},
        )
        mock_session = MagicMock()
        await handler.handle(event, mock_session)
        mock_session.execute.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Auth Use Case 分支覆盖
# ═══════════════════════════════════════════════════════════════════════════════


def _make_auth_use_case(
    *,
    session_repo_mock: AsyncMock | None = None,
    refresh_repo_mock: AsyncMock | None = None,
    auth_port_mock: AsyncMock | None = None,
) -> tuple[AuthUseCase, dict[str, AsyncMock]]:
    """构造带 mock 依赖的 AuthUseCase。"""

    from app.core.security.digest import TokenDigestService
    from app.core.security.password import Argon2Hasher
    from app.modules.auth.use_case import AuthUseCase

    mock_uow = MagicMock()
    mock_session = MagicMock()
    mock_uow.session = mock_session
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=None)
    mock_uow.commit = AsyncMock()

    mock_session_repo = session_repo_mock or AsyncMock()
    mock_refresh_repo = refresh_repo_mock or AsyncMock()
    mock_attempt_repo = AsyncMock()
    mock_auth_port = auth_port_mock or AsyncMock()
    mock_login_log = AsyncMock()
    mock_security_log = AsyncMock()

    uc = AuthUseCase(
        uow_factory=MagicMock(return_value=mock_uow),
        clock=_FixedClock(),
        id_generator=_FixedIdGen(),
        hasher=Argon2Hasher(),
        digest_service=TokenDigestService(
            access_key=b"a" * 32,
            refresh_key=b"b" * 32,
        ),
        user_auth_port_factory=lambda s: mock_auth_port,
        login_log_factory=lambda s: mock_login_log,
        security_log_factory=lambda s: mock_security_log,
    )

    patch.object(uc, "_create_session_repo", return_value=mock_session_repo).start()
    patch.object(uc, "_create_refresh_repo", return_value=mock_refresh_repo).start()
    patch.object(uc, "_create_attempt_repo", return_value=mock_attempt_repo).start()
    patch.object(uc, "_create_login_log", return_value=mock_login_log).start()
    patch.object(uc, "_create_security_log", return_value=mock_security_log).start()

    return uc, {
        "uow": mock_uow,
        "session_repo": mock_session_repo,
        "refresh_repo": mock_refresh_repo,
        "auth_port": mock_auth_port,
        "login_log": mock_login_log,
    }


def _make_mock_session(
    *,
    revoked: bool = False,
    token_expires_at: datetime | None = None,
    last_activity_at: datetime | None = None,
    absolute_expires_at: datetime | None = None,
    user_id: UUID | None = None,
    id: UUID | None = None,
) -> MagicMock:
    """构造 mock 会话对象。"""

    now = _NOW
    session = MagicMock()
    session.id = id or uuid4()
    session.user_id = user_id or uuid4()
    session.revoked = revoked
    session.token_expires_at = token_expires_at or (now + timedelta(minutes=15))
    session.last_activity_at = last_activity_at or now
    session.absolute_expires_at = absolute_expires_at or (now + timedelta(hours=12))
    session.access_token_digest = "fake_digest"
    return session


@pytest.mark.g2
@pytest.mark.unit
class TestAuthUseCaseBranches:
    """auth/use_case.py 分支覆盖。"""

    async def test_authenticate_token_not_found(self) -> None:
        """authenticate Token 不存在返回 None。"""

        mock_repo = AsyncMock()
        mock_repo.get_by_token_digest = AsyncMock(return_value=None)
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        result = await uc.authenticate("nonexistent_token")
        assert result is None

    async def test_authenticate_revoked_session(self) -> None:
        """authenticate 会话已吊销返回 None。"""

        mock_repo = AsyncMock()
        mock_repo.get_by_token_digest = AsyncMock(
            return_value=_make_mock_session(revoked=True),
        )
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        result = await uc.authenticate("some_token")
        assert result is None

    async def test_authenticate_token_expired(self) -> None:
        """authenticate Token 过期返回 None。"""

        mock_repo = AsyncMock()
        mock_repo.get_by_token_digest = AsyncMock(
            return_value=_make_mock_session(
                token_expires_at=_NOW - timedelta(minutes=1),
            ),
        )
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        result = await uc.authenticate("some_token")
        assert result is None

    async def test_authenticate_idle_timeout(self) -> None:
        """authenticate 空闲超时返回 None — 覆盖 idle-timeout 分支。"""

        from app.modules.auth.constants import SESSION_IDLE_TIMEOUT

        mock_repo = AsyncMock()
        mock_repo.get_by_token_digest = AsyncMock(
            return_value=_make_mock_session(
                last_activity_at=_NOW - SESSION_IDLE_TIMEOUT - timedelta(seconds=1),
                # Token 仍有效（15 分钟内）
                token_expires_at=_NOW + timedelta(minutes=5),
            ),
        )
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        result = await uc.authenticate("some_token")
        assert result is None

    async def test_authenticate_absolute_timeout(self) -> None:
        """authenticate 绝对超时返回 None — 覆盖 absolute-timeout 分支。"""

        mock_repo = AsyncMock()
        mock_repo.get_by_token_digest = AsyncMock(
            return_value=_make_mock_session(
                # Token 有效
                token_expires_at=_NOW + timedelta(minutes=5),
                # 空闲未超时
                last_activity_at=_NOW - timedelta(minutes=1),
                # 绝对已过期
                absolute_expires_at=_NOW - timedelta(seconds=1),
            ),
        )
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        result = await uc.authenticate("some_token")
        assert result is None

    async def test_authenticate_disabled_user(self) -> None:
        """authenticate 用户已禁用返回 None。"""

        from app.modules.identity.models import UserStatus

        mock_repo = AsyncMock()
        mock_repo.get_by_token_digest = AsyncMock(
            return_value=_make_mock_session(),
        )
        mock_auth = AsyncMock()
        mock_auth.get_status_by_id = AsyncMock(return_value=UserStatus.DISABLED)
        uc, _ = _make_auth_use_case(
            session_repo_mock=mock_repo,
            auth_port_mock=mock_auth,
        )
        result = await uc.authenticate("some_token")
        assert result is None

    async def test_authenticate_success(self) -> None:
        """authenticate 成功返回 (user_id, session_id)。"""

        from app.modules.identity.models import UserStatus

        user_id = uuid4()
        session_id = uuid4()
        mock_repo = AsyncMock()
        mock_repo.get_by_token_digest = AsyncMock(
            return_value=_make_mock_session(user_id=user_id, id=session_id),
        )
        mock_repo.update_activity_time = AsyncMock()
        mock_auth = AsyncMock()
        mock_auth.get_status_by_id = AsyncMock(return_value=UserStatus.ACTIVE)
        uc, _ = _make_auth_use_case(
            session_repo_mock=mock_repo,
            auth_port_mock=mock_auth,
        )
        result = await uc.authenticate("some_token")
        assert result is not None
        assert result[0] == user_id
        assert result[1] == session_id

    async def test_logout_other_with_multiple_sessions(self) -> None:
        """logout_other 多会话时写日志 — 覆盖 count>0 分支。"""

        current_id = uuid4()
        mock_repo = AsyncMock()
        mock_repo.revoke = AsyncMock()
        mock_repo.list_active_by_user = AsyncMock(
            return_value=[
                _make_mock_session(id=current_id),  # 当前会话——不吊销
                _make_mock_session(id=uuid4()),
                _make_mock_session(id=uuid4()),
            ],
        )
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        result = await uc.logout_other(
            current_session_id=current_id,
            user_id=uuid4(),
            ip_address="127.0.0.1",
            user_agent=None,
            request_id="req",
        )
        assert result.revoked_count == 2  # 3 sessions - 1 current = 2

    async def test_force_offline_with_sessions(self) -> None:
        """force_offline 有会话时吊销 Refresh Token — 覆盖 count>0 和循环分支。"""

        session = _make_mock_session(id=uuid4())
        mock_repo = AsyncMock()
        mock_repo.revoke_all_by_user = AsyncMock(return_value=3)
        mock_repo.list_active_by_user = AsyncMock(return_value=[session])
        mock_refresh = AsyncMock()
        mock_refresh.revoke_by_session = AsyncMock()
        uc, _ = _make_auth_use_case(
            session_repo_mock=mock_repo,
            refresh_repo_mock=mock_refresh,
        )
        from app.application.context import UseCaseContext

        ctx = UseCaseContext(
            request_id="req",
            actor_id=str(uuid4()),
            current_time=_NOW,
            security_metadata=MappingProxyType({}),
        )
        count = await uc.force_offline(
            ctx,
            uuid4(),
            ip_address="127.0.0.1",
            user_agent=None,
        )
        assert count == 3
        mock_refresh.revoke_by_session.assert_called_once()

    async def test_force_offline_no_sessions(self) -> None:
        """force_offline 无会话跳过日志 — 覆盖 count==0 分支。"""

        mock_repo = AsyncMock()
        mock_repo.revoke_all_by_user = AsyncMock(return_value=0)
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        from app.application.context import UseCaseContext

        ctx = UseCaseContext(
            request_id="req",
            actor_id=str(uuid4()),
            current_time=_NOW,
            security_metadata=MappingProxyType({}),
        )
        count = await uc.force_offline(
            ctx,
            uuid4(),
            ip_address="127.0.0.1",
            user_agent=None,
        )
        assert count == 0

    async def test_logout_other_count_zero(self) -> None:
        """logout_other 无其他会话时 count==0 — 覆盖 count==0 跳过日志分支。"""

        current_id = uuid4()
        mock_repo = AsyncMock()
        mock_repo.revoke = AsyncMock()
        mock_repo.list_active_by_user = AsyncMock(
            return_value=[_make_mock_session(id=current_id)],  # 只有当前会话
        )
        uc, _ = _make_auth_use_case(session_repo_mock=mock_repo)
        result = await uc.logout_other(
            current_session_id=current_id,
            user_id=uuid4(),
            ip_address="127.0.0.1",
            user_agent=None,
            request_id="req",
        )
        assert result.revoked_count == 0

    async def test_enable_role_not_found(self) -> None:
        """enable_role 角色不存在。"""

        from app.modules.rbac.errors import RoleNotFoundError

        uc, _ = _make_rbac_use_case(repo_role=None)
        with pytest.raises(RoleNotFoundError):
            await uc.enable_role(_make_ctx(), uuid4())

    async def test_disable_role_not_found(self) -> None:
        """disable_role 角色不存在。"""

        from app.modules.rbac.errors import RoleNotFoundError

        uc, _ = _make_rbac_use_case(repo_role=None)
        with pytest.raises(RoleNotFoundError):
            await uc.disable_role(_make_ctx(), uuid4())


@pytest.mark.g2
@pytest.mark.unit
class TestAuthDependencyBranchCoverage:
    """auth/dependencies.py get_authenticated_context_async 分支覆盖。"""

    async def test_authenticate_context_success(self) -> None:
        """认证成功返回 UseCaseContext — 覆盖 result-is-not-None 分支。"""

        from app.modules.auth.dependencies import get_authenticated_context_async

        user_id = uuid4()
        session_id = uuid4()

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer validtoken"}
        mock_request.scope = {"request_id": "test-req"}

        mock_auth_uc = AsyncMock()
        mock_auth_uc.authenticate = AsyncMock(return_value=(user_id, session_id))

        mock_request.app.state.auth_use_case = mock_auth_uc

        result = await get_authenticated_context_async(mock_request)
        assert result.actor_id == str(user_id)
        assert result.session_id == str(session_id)

    async def test_authenticate_context_failure(self) -> None:
        """认证失败抛 AuthenticationError — 覆盖 result-is-None 分支。"""

        from app.core.errors.exceptions import AuthenticationError
        from app.modules.auth.dependencies import get_authenticated_context_async

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer invalidtoken"}
        mock_request.scope = {"request_id": "test-req"}

        mock_auth_uc = AsyncMock()
        mock_auth_uc.authenticate = AsyncMock(return_value=None)

        mock_request.app.state.auth_use_case = mock_auth_uc

        with pytest.raises(AuthenticationError):
            await get_authenticated_context_async(mock_request)


@pytest.mark.g2
@pytest.mark.unit
class TestRbacUseCaseAdditionalBranches:
    """rbac/use_case.py 额外分支覆盖。"""

    async def test_assign_user_roles_missing_codes(self) -> None:
        """assign_user_roles 目标角色不存在 — 覆盖 missing-codes 分支。"""

        from app.modules.rbac.errors import RoleNotFoundError

        uc, _ = _make_rbac_use_case(
            user_exists=True,
            repo_roles_by_codes=[],
            actor_permissions=frozenset(),
        )
        with pytest.raises(RoleNotFoundError):
            await uc.assign_user_roles(
                _make_ctx(),
                uuid4(),
                AssignUserRolesRequest(role_codes=["nonexistent_role"]),
            )

    async def test_remove_user_role_super_admin_check(self) -> None:
        """remove_user_role 移除超管角色触发保护检查 — 覆盖 super-admin 分支。"""

        from app.core.security.authorization import SUPER_ADMIN_ROLE_CODE
        from app.modules.rbac.errors import UserRoleNotAssignedError

        uc, mocks = _make_rbac_use_case(
            repo_role=_make_role(code=SUPER_ADMIN_ROLE_CODE),
            user_exists=True,
            actor_permissions=frozenset(),
        )
        mocks["repo"].remove_user_role = AsyncMock(return_value=False)

        mock_rbac = AsyncMock()
        mock_rbac.get_role_codes_by_user = AsyncMock(return_value=set())
        patch.object(uc, "_create_user_rbac_port", return_value=mock_rbac).start()

        with pytest.raises(UserRoleNotAssignedError):
            await uc.remove_user_role(_make_ctx(), uuid4(), uuid4())


# ═══════════════════════════════════════════════════════════════════════════════
# Adapter 分支覆盖 — 直接测试 adapter 边界分支
# ═══════════════════════════════════════════════════════════════════════════════


def _mock_execute_result(
    *,
    scalar: object | None = None,
    scalars_list: list[object] | None = None,
    scalar_or_zero: int | None = None,
) -> MagicMock:
    """构造 mock execute 结果。"""

    result = MagicMock()
    if scalar is not None:
        result.scalar_one_or_none = MagicMock(return_value=scalar)
    elif scalar_or_zero is not None:
        result.scalar = MagicMock(return_value=scalar_or_zero)
    else:
        result.scalar_one_or_none = MagicMock(return_value=None)
        result.scalar = MagicMock(return_value=0)
    if scalars_list is not None:
        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=scalars_list)
        result.scalars = MagicMock(return_value=scalars_mock)
    return result


@pytest.mark.g2
@pytest.mark.unit
class TestIdentityAdapterBranches:
    """identity/adapter.py 分支覆盖。"""

    async def test_list_users_no_filter_no_sort(self) -> None:
        """list_users 无状态筛选、无排序 — 覆盖 status=None 和 sort=[] 分支。"""

        from app.modules.identity.adapter import SqlAlchemyUserRepository

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(
                scalar_or_zero=0,
                scalars_list=[],
            ),
        )
        repo = SqlAlchemyUserRepository(mock_session)
        users, total = await repo.list_users(
            offset=0,
            limit=10,
            sort_fields=[],
            status_filter=None,
        )
        assert users == []
        assert total == 0

    async def test_list_users_with_filter_and_sort(self) -> None:
        """list_users 有状态筛选、有排序 — 覆盖 status!=None 和 sort!={} 分支。"""

        from app.core.api.pagination import SortField, SortOrder
        from app.modules.identity.adapter import SqlAlchemyUserRepository
        from app.modules.identity.models import UserStatus

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(
                scalar_or_zero=1,
                scalars_list=[],
            ),
        )
        repo = SqlAlchemyUserRepository(mock_session)
        users, total = await repo.list_users(
            offset=0,
            limit=10,
            sort_fields=[SortField(name="username", order=SortOrder.ASC)],
            status_filter=UserStatus.ACTIVE,
        )
        assert total == 1

    async def test_save_user_not_found(self) -> None:
        """save 用户不存在 — 覆盖 orm-is-None 分支。"""

        from app.modules.identity.adapter import SqlAlchemyUserRepository
        from app.modules.identity.errors import UserNotFoundError

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(scalar=None),
        )
        repo = SqlAlchemyUserRepository(mock_session)
        with pytest.raises(UserNotFoundError):
            await repo.save(_make_user())

    async def test_delete_by_id_not_found(self) -> None:
        """delete_by_id 用户不存在 — 覆盖 orm-is-None 返回 False 分支。"""

        from app.modules.identity.adapter import SqlAlchemyUserRepository

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(scalar=None),
        )
        repo = SqlAlchemyUserRepository(mock_session)
        result = await repo.delete_by_id(uuid4())
        assert result is False

    async def test_count_active_users_empty_set(self) -> None:
        """count_active_users_by_ids 空集合 — 覆盖 not-user-ids 分支。"""

        from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter

        mock_session = AsyncMock()
        adapter = SqlAlchemyUserAuthAdapter(mock_session)
        result = await adapter.count_active_users_by_ids(set())
        assert result == 0


@pytest.mark.g2
@pytest.mark.unit
class TestRbacAdapterBranches:
    """rbac/adapter.py 分支覆盖。"""

    async def test_list_roles_with_filter(self) -> None:
        """list_roles 有状态筛选 — 覆盖 status!=None 分支。"""

        from app.core.api.pagination import SortField, SortOrder
        from app.modules.rbac.adapter import SqlAlchemyRbacRepository
        from app.modules.rbac.models import RoleStatus

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(
                scalar_or_zero=0,
                scalars_list=[],
            ),
        )
        repo = SqlAlchemyRbacRepository(mock_session)
        roles, total = await repo.list_roles(
            offset=0,
            limit=10,
            sort_fields=[SortField(name="code", order=SortOrder.ASC)],
            status_filter=RoleStatus.ACTIVE,
        )
        assert roles == []
        assert total == 0

    async def test_list_roles_no_filter_no_sort(self) -> None:
        """list_roles 无筛选、无排序 — 覆盖 status=None 分支。"""

        from app.modules.rbac.adapter import SqlAlchemyRbacRepository

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(
                scalar_or_zero=0,
                scalars_list=[],
            ),
        )
        repo = SqlAlchemyRbacRepository(mock_session)
        roles, total = await repo.list_roles(
            offset=0,
            limit=10,
            sort_fields=[],
            status_filter=None,
        )
        assert total == 0

    async def test_save_role_not_found(self) -> None:
        """save_role 角色不存在 — 覆盖 orm-is-None 分支。"""

        from app.modules.rbac.adapter import SqlAlchemyRbacRepository
        from app.modules.rbac.errors import RoleNotFoundError

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(scalar=None),
        )
        repo = SqlAlchemyRbacRepository(mock_session)
        with pytest.raises(RoleNotFoundError):
            await repo.save_role(_make_role())

    async def test_delete_role_by_id_not_found(self) -> None:
        """delete_role_by_id 角色不存在 — 覆盖 orm-is-None 分支。"""

        from app.modules.rbac.adapter import SqlAlchemyRbacRepository

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_mock_execute_result(scalar=None),
        )
        repo = SqlAlchemyRbacRepository(mock_session)
        result = await repo.delete_role_by_id(uuid4())
        assert result is False

    async def test_get_permission_codes_empty(self) -> None:
        """get_permission_codes 空集合 — 覆盖 not-codes 分支。"""

        from app.modules.rbac.adapter import SqlAlchemyRbacRepository

        mock_session = AsyncMock()
        repo = SqlAlchemyRbacRepository(mock_session)
        result = await repo.get_permission_codes(set())
        assert result == []

    async def test_delete_permissions_by_ids_empty(self) -> None:
        """delete_permissions_by_ids 空集合 — 覆盖 not-ids 分支。"""

        from app.modules.rbac.adapter import SqlAlchemyRbacRepository

        mock_session = AsyncMock()
        repo = SqlAlchemyRbacRepository(mock_session)
        result = await repo.delete_permissions_by_ids(set())
        assert result == 0
