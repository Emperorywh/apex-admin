"""组织模块错误码与异常 — SPEC 10.1 / 10.2 / 14.1 / 14.2 / 14.3.

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

#: 部门不存在 — 按 ID 或编码查询或操作部门但未找到。
ORG_DEPT_NOT_FOUND = "ORG.DEPT_NOT_FOUND"

#: 部门编码已存在 — 创建部门时编码已被占用。
ORG_DEPT_ALREADY_EXISTS = "ORG.DEPT_ALREADY_EXISTS"

#: 部门已禁用 — 尝试禁用已处于禁用状态的部门。
ORG_DEPT_ALREADY_DISABLED = "ORG.DEPT_ALREADY_DISABLED"

#: 部门已启用 — 尝试启用已处于启用状态的部门。
ORG_DEPT_ALREADY_ACTIVE = "ORG.DEPT_ALREADY_ACTIVE"

#: 部门仍有子部门 — 尝试删除仍有子部门的部门。
ORG_DEPT_HAS_CHILDREN = "ORG.DEPT_HAS_CHILDREN"

#: 部门仍有用户 — 尝试删除仍有用户关联的部门。
ORG_DEPT_HAS_USERS = "ORG.DEPT_HAS_USERS"

#: 部门循环层级 — 调整层级时目标父部门是自身或自身后代，会形成循环。
ORG_DEPT_CYCLE_DETECTED = "ORG.DEPT_CYCLE_DETECTED"

#: 父部门无效 — 指定的父部门不存在或处于禁用状态。
ORG_DEPT_INVALID_PARENT = "ORG.DEPT_INVALID_PARENT"

# ── 岗位错误码 — SPEC 14.2 ──────────────────────────────────────────────────

#: 岗位不存在 — 按 ID 或编码查询或操作岗位但未找到。
ORG_POST_NOT_FOUND = "ORG.POST_NOT_FOUND"

#: 岗位编码已存在 — 创建岗位时编码已被占用。
ORG_POST_ALREADY_EXISTS = "ORG.POST_ALREADY_EXISTS"

#: 岗位已禁用 — 尝试禁用已处于禁用状态的岗位。
ORG_POST_ALREADY_DISABLED = "ORG.POST_ALREADY_DISABLED"

#: 岗位已启用 — 尝试启用已处于启用状态的岗位。
ORG_POST_ALREADY_ACTIVE = "ORG.POST_ALREADY_ACTIVE"

#: 岗位仍有用户 — 尝试删除仍有用户关联的岗位。
ORG_POST_HAS_USERS = "ORG.POST_HAS_USERS"

# ── 用户组织关系错误码 — SPEC 14.3 ──────────────────────────────────────────

#: 用户已有主部门 — 用户已设置主部门，需先解除再分配。
ORG_USER_ALREADY_HAS_DEPARTMENT = "ORG.USER_ALREADY_HAS_DEPARTMENT"

#: 用户岗位分配重复 — 用户已被分配该岗位（幂等防护）。
ORG_USER_POST_DUPLICATE = "ORG.USER_POST_DUPLICATE"

#: 用户部门关系不存在 — 尝试解除不存在的主部门关系。
ORG_USER_DEPT_NOT_FOUND = "ORG.USER_DEPT_NOT_FOUND"

#: 用户岗位关系不存在 — 尝试移除不存在的用户岗位关系。
ORG_USER_POST_NOT_FOUND = "ORG.USER_POST_NOT_FOUND"

#: 岗位已禁用 — 尝试为用户分配已禁用的岗位。
ORG_POST_DISABLED = "ORG.POST_DISABLED"

#: 部门已禁用 — 尝试为用户分配已禁用的部门。
ORG_DEPT_DISABLED = "ORG.DEPT_DISABLED"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class DepartmentNotFoundError(NotFoundError):
    """部门不存在 — HTTP 404."""

    code = ORG_DEPT_NOT_FOUND


class DepartmentAlreadyExistsError(ConflictError):
    """部门编码冲突 — HTTP 409."""

    code = ORG_DEPT_ALREADY_EXISTS


class DepartmentAlreadyDisabledError(ConflictError):
    """部门已禁用 — HTTP 409."""

    code = ORG_DEPT_ALREADY_DISABLED


class DepartmentAlreadyActiveError(ConflictError):
    """部门已启用 — HTTP 409."""

    code = ORG_DEPT_ALREADY_ACTIVE


class DepartmentHasChildrenError(ConflictError):
    """部门仍有子部门 — HTTP 409.

    SPEC 14.1: "有用户或子部门时的删除规则明确"。
    存在子部门时拒绝删除。
    """

    code = ORG_DEPT_HAS_CHILDREN


class DepartmentHasUsersError(ConflictError):
    """部门仍有用户 — HTTP 409.

    SPEC 14.1: "有用户或子部门时的删除规则明确"。
    存在用户关联时拒绝删除。
    用户组织关系在 TASK-020 实现；此异常保留供删除保护规则使用。
    """

    code = ORG_DEPT_HAS_USERS


class DepartmentCycleError(ConflictError):
    """部门循环层级 — HTTP 409.

    SPEC 14.1: "防止形成循环层级"。
    调整层级时目标父部门是自身或自身后代，会形成循环。
    """

    code = ORG_DEPT_CYCLE_DETECTED


class InvalidParentError(ParameterError):
    """父部门无效 — HTTP 400.

    指定的父部门不存在或处于禁用状态（禁用部门不能作为父部门）。
    """

    code = ORG_DEPT_INVALID_PARENT


# ── 岗位异常类 — SPEC 14.2 ──────────────────────────────────────────────────


class PostNotFoundError(NotFoundError):
    """岗位不存在 — HTTP 404."""

    code = ORG_POST_NOT_FOUND


class PostAlreadyExistsError(ConflictError):
    """岗位编码冲突 — HTTP 409."""

    code = ORG_POST_ALREADY_EXISTS


class PostAlreadyDisabledError(ConflictError):
    """岗位已禁用 — HTTP 409."""

    code = ORG_POST_ALREADY_DISABLED


class PostAlreadyActiveError(ConflictError):
    """岗位已启用 — HTTP 409."""

    code = ORG_POST_ALREADY_ACTIVE


class PostHasUsersError(ConflictError):
    """岗位仍有用户 — HTTP 409.

    SPEC 14.2: 存在用户岗位关联时拒绝删除岗位。
    """

    code = ORG_POST_HAS_USERS


class PostDisabledError(ConflictError):
    """岗位已禁用 — HTTP 409.

    尝试为用户分配已禁用的岗位。
    """

    code = ORG_POST_DISABLED


class DepartmentDisabledError(ConflictError):
    """部门已禁用 — HTTP 409.

    尝试为用户分配已禁用的部门。
    """

    code = ORG_DEPT_DISABLED


# ── 用户组织关系异常类 — SPEC 14.3 ──────────────────────────────────────────


class UserAlreadyHasDepartmentError(ConflictError):
    """用户已有主部门 — HTTP 409.

    SPEC 14.3: 基座默认仅主部门。
    用户已设置主部门时，需先解除再分配。
    """

    code = ORG_USER_ALREADY_HAS_DEPARTMENT


class UserPostDuplicateError(ConflictError):
    """用户岗位分配重复 — HTTP 409.

    用户已被分配该岗位。分配操作幂等——已存在时返回成功，
    此异常仅用于需要明确拒绝重复分配的场景。
    """

    code = ORG_USER_POST_DUPLICATE


class UserDepartmentNotFoundError(ConflictError):
    """用户部门关系不存在 — HTTP 409.

    尝试解除不存在的主部门关系。
    """

    code = ORG_USER_DEPT_NOT_FOUND


class UserPostNotFoundError(ConflictError):
    """用户岗位关系不存在 — HTTP 409.

    尝试移除不存在的用户岗位关系。
    """

    code = ORG_USER_POST_NOT_FOUND


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    ORG_DEPT_NOT_FOUND,
    404,
    meaning="部门不存在",
    scenario="按 ID 或编码查询或操作部门但未找到时使用",
)
default_registry.register(
    ORG_DEPT_ALREADY_EXISTS,
    409,
    meaning="部门编码已存在",
    scenario="创建部门时编码已被占用",
)
default_registry.register(
    ORG_DEPT_ALREADY_DISABLED,
    409,
    meaning="部门已禁用",
    scenario="尝试禁用已处于禁用状态的部门",
)
default_registry.register(
    ORG_DEPT_ALREADY_ACTIVE,
    409,
    meaning="部门已启用",
    scenario="尝试启用已处于启用状态的部门",
)
default_registry.register(
    ORG_DEPT_HAS_CHILDREN,
    409,
    meaning="部门仍有子部门",
    scenario="尝试删除仍有子部门的部门（SPEC 14.1 删除保护规则）",
)
default_registry.register(
    ORG_DEPT_HAS_USERS,
    409,
    meaning="部门仍有用户",
    scenario="尝试删除仍有用户关联的部门（SPEC 14.1 删除保护规则）",
)
default_registry.register(
    ORG_DEPT_CYCLE_DETECTED,
    409,
    meaning="部门循环层级",
    scenario="调整层级时目标父部门是自身或自身后代（SPEC 14.1 循环防护）",
)
default_registry.register(
    ORG_DEPT_INVALID_PARENT,
    400,
    meaning="父部门无效",
    scenario="指定的父部门不存在或处于禁用状态",
)

# 岗位错误码注册
default_registry.register(
    ORG_POST_NOT_FOUND,
    404,
    meaning="岗位不存在",
    scenario="按 ID 或编码查询或操作岗位但未找到时使用",
)
default_registry.register(
    ORG_POST_ALREADY_EXISTS,
    409,
    meaning="岗位编码已存在",
    scenario="创建岗位时编码已被占用",
)
default_registry.register(
    ORG_POST_ALREADY_DISABLED,
    409,
    meaning="岗位已禁用",
    scenario="尝试禁用已处于禁用状态的岗位",
)
default_registry.register(
    ORG_POST_ALREADY_ACTIVE,
    409,
    meaning="岗位已启用",
    scenario="尝试启用已处于启用状态的岗位",
)
default_registry.register(
    ORG_POST_HAS_USERS,
    409,
    meaning="岗位仍有用户",
    scenario="尝试删除仍有用户关联的岗位",
)
default_registry.register(
    ORG_POST_DISABLED,
    409,
    meaning="岗位已禁用",
    scenario="尝试为用户分配已禁用的岗位",
)
default_registry.register(
    ORG_DEPT_DISABLED,
    409,
    meaning="部门已禁用",
    scenario="尝试为用户分配已禁用的部门",
)
default_registry.register(
    ORG_USER_ALREADY_HAS_DEPARTMENT,
    409,
    meaning="用户已有主部门",
    scenario="用户已设置主部门，需先解除再分配（SPEC 14.3）",
)
default_registry.register(
    ORG_USER_POST_DUPLICATE,
    409,
    meaning="用户岗位分配重复",
    scenario="用户已被分配该岗位（SPEC 14.2 分配幂等）",
)
default_registry.register(
    ORG_USER_DEPT_NOT_FOUND,
    409,
    meaning="用户部门关系不存在",
    scenario="尝试解除不存在的主部门关系",
)
default_registry.register(
    ORG_USER_POST_NOT_FOUND,
    409,
    meaning="用户岗位关系不存在",
    scenario="尝试移除不存在的用户岗位关系",
)
