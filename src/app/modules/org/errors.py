"""组织模块错误码与异常 — SPEC 10.1 / 10.2 / 14.1.

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
