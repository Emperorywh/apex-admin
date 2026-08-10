"""RBAC 模块单元测试 — SPEC 13.1 / 13.2 / 18.2 / 5.7.

覆盖:
  - 领域实体、状态枚举与 Schema 验证（不连接数据库）。
  - 错误码注册与异常类型。
  - Schema 拒绝未知字段（extra="forbid"）。
  - 权限编码格式校验。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.rbac.errors import (
    RBAC_BUILTIN_ROLE_PROTECTED,
    RBAC_PERMISSION_NOT_FOUND,
    RBAC_ROLE_ALREADY_ACTIVE,
    RBAC_ROLE_ALREADY_DISABLED,
    RBAC_ROLE_ALREADY_EXISTS,
    RBAC_ROLE_HAS_USERS,
    RBAC_ROLE_NOT_FOUND,
    RBAC_USER_ROLE_ALREADY_ASSIGNED,
    RBAC_USER_ROLE_NOT_ASSIGNED,
    BuiltinRoleProtectedError,
    PermissionNotFoundError,
    RoleAlreadyActiveError,
    RoleAlreadyDisabledError,
    RoleAlreadyExistsError,
    RoleHasUsersError,
    RoleNotFoundError,
    UserRoleAlreadyAssignedError,
    UserRoleNotAssignedError,
)
from app.modules.rbac.models import Permission, Role, RoleAssignment, RoleStatus

# ═══════════════════════════════════════════════════════════════════════════════
# 领域实体与状态枚举
# ═══════════════════════════════════════════════════════════════════════════════


def _make_role(
    *,
    is_builtin: bool = False,
    status: RoleStatus = RoleStatus.ACTIVE,
) -> Role:
    """构造测试用角色实体。"""

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Role(
        id=uuid4(),
        code="editor",
        display_name="编辑者",
        description="内容编辑角色",
        status=status,
        is_builtin=is_builtin,
        sort_order=0,
        created_at=now,
        updated_at=now,
        created_by="admin",
        updated_by="admin",
    )


@pytest.mark.g2
@pytest.mark.unit
class TestRoleStatus:
    """角色状态枚举 — SPEC 8.3 / 13.2."""

    def test_active_value(self) -> None:
        assert RoleStatus.ACTIVE.value == "active"

    def test_disabled_value(self) -> None:
        assert RoleStatus.DISABLED.value == "disabled"

    def test_from_string(self) -> None:
        assert RoleStatus("active") == RoleStatus.ACTIVE
        assert RoleStatus("disabled") == RoleStatus.DISABLED

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            RoleStatus("invalid")


@pytest.mark.g2
@pytest.mark.unit
class TestRoleEntity:
    """角色领域实体 — SPEC 13.1 / 13.2."""

    def test_role_is_frozen(self) -> None:
        role = _make_role()
        with pytest.raises(AttributeError):
            role.display_name = "changed"  # type: ignore[misc]

    def test_role_fields(self) -> None:
        role = _make_role()
        assert role.code == "editor"
        assert role.display_name == "编辑者"
        assert role.status == RoleStatus.ACTIVE
        assert role.is_builtin is False

    def test_builtin_role(self) -> None:
        role = _make_role(is_builtin=True)
        assert role.is_builtin is True


@pytest.mark.g2
@pytest.mark.unit
class TestPermissionEntity:
    """权限点领域实体 — SPEC 13.1."""

    def test_permission_is_frozen(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        perm = Permission(
            id=uuid4(),
            code="system:user:read",
            display_name="读取用户",
            description=None,
            module_code="identity",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(AttributeError):
            perm.code = "changed"  # type: ignore[misc]


@pytest.mark.g2
@pytest.mark.unit
class TestRoleAssignment:
    """用户角色分配记录 — SPEC 13.1."""

    def test_assignment_is_frozen(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        assignment = RoleAssignment(
            user_id=uuid4(),
            role_id=uuid4(),
            created_at=now,
            created_by="admin",
        )
        with pytest.raises(AttributeError):
            assignment.user_id = uuid4()  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# 错误码与异常
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestErrorCodes:
    """错误码注册与异常类型 — SPEC 10.1 / 10.2."""

    def test_role_not_found_code(self) -> None:
        assert RBAC_ROLE_NOT_FOUND == "RBAC.ROLE_NOT_FOUND"
        assert RoleNotFoundError.code == RBAC_ROLE_NOT_FOUND

    def test_role_already_exists_code(self) -> None:
        assert RBAC_ROLE_ALREADY_EXISTS == "RBAC.ROLE_ALREADY_EXISTS"
        assert RoleAlreadyExistsError.code == RBAC_ROLE_ALREADY_EXISTS

    def test_role_already_disabled_code(self) -> None:
        assert RBAC_ROLE_ALREADY_DISABLED == "RBAC.ROLE_ALREADY_DISABLED"
        assert RoleAlreadyDisabledError.code == RBAC_ROLE_ALREADY_DISABLED

    def test_role_already_active_code(self) -> None:
        assert RBAC_ROLE_ALREADY_ACTIVE == "RBAC.ROLE_ALREADY_ACTIVE"
        assert RoleAlreadyActiveError.code == RBAC_ROLE_ALREADY_ACTIVE

    def test_permission_not_found_code(self) -> None:
        assert RBAC_PERMISSION_NOT_FOUND == "RBAC.PERMISSION_NOT_FOUND"
        assert PermissionNotFoundError.code == RBAC_PERMISSION_NOT_FOUND

    def test_builtin_role_protected_code(self) -> None:
        assert RBAC_BUILTIN_ROLE_PROTECTED == "RBAC.BUILTIN_ROLE_PROTECTED"
        assert BuiltinRoleProtectedError.code == RBAC_BUILTIN_ROLE_PROTECTED

    def test_user_role_already_assigned_code(self) -> None:
        assert RBAC_USER_ROLE_ALREADY_ASSIGNED == "RBAC.USER_ROLE_ALREADY_ASSIGNED"
        assert UserRoleAlreadyAssignedError.code == RBAC_USER_ROLE_ALREADY_ASSIGNED

    def test_user_role_not_assigned_code(self) -> None:
        assert RBAC_USER_ROLE_NOT_ASSIGNED == "RBAC.USER_ROLE_NOT_ASSIGNED"
        assert UserRoleNotAssignedError.code == RBAC_USER_ROLE_NOT_ASSIGNED

    def test_role_has_users_code(self) -> None:
        assert RBAC_ROLE_HAS_USERS == "RBAC.ROLE_HAS_USERS"
        assert RoleHasUsersError.code == RBAC_ROLE_HAS_USERS

    def test_permission_not_found_is_parameter_error(self) -> None:
        """权限点不存在返回参数错误（HTTP 400）— SPEC 10.1."""

        from app.core.errors.exceptions import ParameterError

        assert issubclass(PermissionNotFoundError, ParameterError)


# ═══════════════════════════════════════════════════════════════════════════════
# Schema 验证
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestSchemas:
    """请求 Schema 验证 — SPEC 9.2."""

    def test_role_create_rejects_unknown_field(self) -> None:
        from app.modules.rbac.schemas import RoleCreateRequest

        with pytest.raises(ValidationError):
            RoleCreateRequest(
                code="editor",
                display_name="编辑者",
                is_builtin=True,  # type: ignore[call-arg]
            )

    def test_role_create_validates_code_pattern(self) -> None:
        from app.modules.rbac.schemas import RoleCreateRequest

        # 大写字母被拒绝
        with pytest.raises(ValidationError):
            RoleCreateRequest(code="Editor", display_name="编辑者")

    def test_role_create_accepts_valid(self) -> None:
        from app.modules.rbac.schemas import RoleCreateRequest

        req = RoleCreateRequest(code="editor", display_name="编辑者")
        assert req.code == "editor"
        assert req.sort_order == 0

    def test_role_update_rejects_unknown_field(self) -> None:
        from app.modules.rbac.schemas import RoleUpdateRequest

        with pytest.raises(ValidationError):
            RoleUpdateRequest(
                display_name="编辑者",
                code="changed",  # type: ignore[call-arg]
            )

    def test_assign_permissions_rejects_unknown_field(self) -> None:
        from app.modules.rbac.schemas import AssignPermissionsRequest

        with pytest.raises(ValidationError):
            AssignPermissionsRequest(
                permission_codes=["system:user:read"],
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_assign_permissions_accepts_empty_list(self) -> None:
        from app.modules.rbac.schemas import AssignPermissionsRequest

        req = AssignPermissionsRequest(permission_codes=[])
        assert req.permission_codes == []

    def test_assign_user_roles_rejects_unknown_field(self) -> None:
        from app.modules.rbac.schemas import AssignUserRolesRequest

        with pytest.raises(ValidationError):
            AssignUserRolesRequest(
                role_codes=["editor"],
                user_id="bad",  # type: ignore[call-arg]
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 同步声明收集
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestCollectDeclaredPermissions:
    """权限点声明收集 — SPEC 25.2."""

    def test_collects_from_all_modules(self) -> None:
        from app.modules.rbac.sync import collect_declared_permissions

        declared = collect_declared_permissions()
        # identity 模块声明的权限点
        assert "system:user:read" in declared
        assert "system:user:write" in declared
        # rbac 模块自身声明的权限点
        assert "rbac:role:read" in declared
        assert "rbac:role:write" in declared

    def test_collects_module_code_mapping(self) -> None:
        from app.modules.rbac.sync import collect_declared_permissions

        declared = collect_declared_permissions()
        assert declared["system:user:read"] == "identity"
        assert declared["rbac:role:read"] == "rbac"
