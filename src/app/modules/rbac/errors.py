"""RBAC 模块错误码与异常 — SPEC 10.1 / 10.2 / 13.2.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间。
  - 错误码与展示文案分离。
  - 每个错误码具有含义、HTTP 状态码和适用场景。

模块错误码在导入时注册到框架默认注册表 ``default_registry``，
使 API 边界的异常处理器能查找对应的 HTTP 状态码和含义元数据
（SPEC 10.1）。同一错误码也在 ``ModuleDefinition`` 中声明，
供 ``modules validate`` 检测全局重复（SPEC 5.5）。
"""

from __future__ import annotations

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import ConflictError, NotFoundError, ParameterError

# ── 错误码常量 ──────────────────────────────────────────────────────────────

#: 角色不存在 — 按 ID 或编码查询或操作角色但未找到。
RBAC_ROLE_NOT_FOUND = "RBAC.ROLE_NOT_FOUND"

#: 角色编码已存在 — 创建角色时编码已被占用。
RBAC_ROLE_ALREADY_EXISTS = "RBAC.ROLE_ALREADY_EXISTS"

#: 角色已禁用 — 尝试禁用已处于禁用状态的角色。
RBAC_ROLE_ALREADY_DISABLED = "RBAC.ROLE_ALREADY_DISABLED"

#: 角色已启用 — 尝试启用已处于启用状态的角色。
RBAC_ROLE_ALREADY_ACTIVE = "RBAC.ROLE_ALREADY_ACTIVE"

#: 权限点不存在 — 分配权限时提供了不在权限目录中的权限编码。
RBAC_PERMISSION_NOT_FOUND = "RBAC.PERMISSION_NOT_FOUND"

#: 内置角色受保护 — 尝试删除或禁用系统内置角色。
RBAC_BUILTIN_ROLE_PROTECTED = "RBAC.BUILTIN_ROLE_PROTECTED"

#: 用户角色已分配 — 尝试重复分配已存在的用户角色关系。
RBAC_USER_ROLE_ALREADY_ASSIGNED = "RBAC.USER_ROLE_ALREADY_ASSIGNED"

#: 用户角色未分配 — 尝试移除不存在的用户角色关系。
RBAC_USER_ROLE_NOT_ASSIGNED = "RBAC.USER_ROLE_NOT_ASSIGNED"

#: 角色仍有用户分配 — 尝试操作仍有用户关联的角色（如删除）。
RBAC_ROLE_HAS_USERS = "RBAC.ROLE_HAS_USERS"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class RoleNotFoundError(NotFoundError):
    """角色不存在 — HTTP 404."""

    code = RBAC_ROLE_NOT_FOUND


class RoleAlreadyExistsError(ConflictError):
    """角色编码冲突 — HTTP 409."""

    code = RBAC_ROLE_ALREADY_EXISTS


class RoleAlreadyDisabledError(ConflictError):
    """角色已禁用 — HTTP 409."""

    code = RBAC_ROLE_ALREADY_DISABLED


class RoleAlreadyActiveError(ConflictError):
    """角色已启用 — HTTP 409."""

    code = RBAC_ROLE_ALREADY_ACTIVE


class PermissionNotFoundError(ParameterError):
    """权限点不存在 — HTTP 400.

    分配权限时提供了不在权限目录中的权限编码（SPEC 13.2: "为角色分配权限点"）。
    """

    code = RBAC_PERMISSION_NOT_FOUND


class BuiltinRoleProtectedError(ConflictError):
    """内置角色受保护 — HTTP 409.

    SPEC 13.2: "系统内置角色具有明确保护规则"。
    尝试删除或禁用系统内置角色时拒绝。
    """

    code = RBAC_BUILTIN_ROLE_PROTECTED


class UserRoleAlreadyAssignedError(ConflictError):
    """用户角色已分配 — HTTP 409."""

    code = RBAC_USER_ROLE_ALREADY_ASSIGNED


class UserRoleNotAssignedError(ConflictError):
    """用户角色未分配 — HTTP 409."""

    code = RBAC_USER_ROLE_NOT_ASSIGNED


class RoleHasUsersError(ConflictError):
    """角色仍有用户分配 — HTTP 409."""

    code = RBAC_ROLE_HAS_USERS


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    RBAC_ROLE_NOT_FOUND,
    404,
    meaning="角色不存在",
    scenario="按 ID 或编码查询或操作角色但未找到时使用",
)
default_registry.register(
    RBAC_ROLE_ALREADY_EXISTS,
    409,
    meaning="角色编码已存在",
    scenario="创建角色时编码已被占用",
)
default_registry.register(
    RBAC_ROLE_ALREADY_DISABLED,
    409,
    meaning="角色已禁用",
    scenario="尝试禁用已处于禁用状态的角色",
)
default_registry.register(
    RBAC_ROLE_ALREADY_ACTIVE,
    409,
    meaning="角色已启用",
    scenario="尝试启用已处于启用状态的角色",
)
default_registry.register(
    RBAC_PERMISSION_NOT_FOUND,
    400,
    meaning="权限点不存在",
    scenario="分配权限时提供了不在权限目录中的权限编码",
)
default_registry.register(
    RBAC_BUILTIN_ROLE_PROTECTED,
    409,
    meaning="内置角色受保护",
    scenario="尝试删除或禁用系统内置角色（SPEC 13.2）",
)
default_registry.register(
    RBAC_USER_ROLE_ALREADY_ASSIGNED,
    409,
    meaning="用户角色已分配",
    scenario="尝试重复分配已存在的用户角色关系",
)
default_registry.register(
    RBAC_USER_ROLE_NOT_ASSIGNED,
    409,
    meaning="用户角色未分配",
    scenario="尝试移除不存在的用户角色关系",
)
default_registry.register(
    RBAC_ROLE_HAS_USERS,
    409,
    meaning="角色仍有用户分配",
    scenario="尝试删除仍有用户关联的角色",
)
