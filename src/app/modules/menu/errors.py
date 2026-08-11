"""菜单模块错误码与异常 — SPEC 10.1 / 10.2 / 15.1.

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

#: 菜单不存在 — 按 ID 查询或操作菜单但未找到。
MENU_NOT_FOUND = "MENU.NOT_FOUND"

#: 菜单已禁用 — 尝试禁用已处于禁用状态的菜单。
MENU_ALREADY_DISABLED = "MENU.ALREADY_DISABLED"

#: 菜单已启用 — 尝试启用已处于启用状态的菜单。
MENU_ALREADY_ACTIVE = "MENU.ALREADY_ACTIVE"

#: 菜单循环层级 — 调整层级时目标父菜单是自身或自身后代，会形成循环。
MENU_CYCLE_DETECTED = "MENU.CYCLE_DETECTED"

#: 父菜单无效 — 指定的父菜单不存在或处于禁用状态。
MENU_INVALID_PARENT = "MENU.INVALID_PARENT"

#: 菜单仍有子菜单 — 尝试删除仍有子菜单的菜单。
MENU_HAS_CHILDREN = "MENU.HAS_CHILDREN"

#: 菜单类型无效 — 创建菜单时指定了无效的菜单类型。
MENU_INVALID_TYPE = "MENU.INVALID_TYPE"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class MenuNotFoundError(NotFoundError):
    """菜单不存在 — HTTP 404."""

    code = MENU_NOT_FOUND


class MenuAlreadyDisabledError(ConflictError):
    """菜单已禁用 — HTTP 409."""

    code = MENU_ALREADY_DISABLED


class MenuAlreadyActiveError(ConflictError):
    """菜单已启用 — HTTP 409."""

    code = MENU_ALREADY_ACTIVE


class MenuCycleError(ConflictError):
    """菜单循环层级 — HTTP 409.

    SPEC 15.1: "防止形成循环层级"。
    调整层级时目标父菜单是自身或自身后代，会形成循环。
    """

    code = MENU_CYCLE_DETECTED


class InvalidMenuParentError(ParameterError):
    """父菜单无效 — HTTP 400.

    指定的父菜单不存在或处于禁用状态（禁用菜单不能作为父菜单）。
    """

    code = MENU_INVALID_PARENT


class MenuHasChildrenError(ConflictError):
    """菜单仍有子菜单 — HTTP 409.

    存在子菜单时拒绝删除。
    """

    code = MENU_HAS_CHILDREN


class InvalidMenuTypeError(ParameterError):
    """菜单类型无效 — HTTP 400."""

    code = MENU_INVALID_TYPE


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    MENU_NOT_FOUND,
    404,
    meaning="菜单不存在",
    scenario="按 ID 查询或操作菜单但未找到时使用",
)
default_registry.register(
    MENU_ALREADY_DISABLED,
    409,
    meaning="菜单已禁用",
    scenario="尝试禁用已处于禁用状态的菜单",
)
default_registry.register(
    MENU_ALREADY_ACTIVE,
    409,
    meaning="菜单已启用",
    scenario="尝试启用已处于启用状态的菜单",
)
default_registry.register(
    MENU_CYCLE_DETECTED,
    409,
    meaning="菜单循环层级",
    scenario="调整层级时目标父菜单是自身或自身后代（SPEC 15.1 循环防护）",
)
default_registry.register(
    MENU_INVALID_PARENT,
    400,
    meaning="父菜单无效",
    scenario="指定的父菜单不存在或处于禁用状态",
)
default_registry.register(
    MENU_HAS_CHILDREN,
    409,
    meaning="菜单仍有子菜单",
    scenario="尝试删除仍有子菜单的菜单",
)
default_registry.register(
    MENU_INVALID_TYPE,
    400,
    meaning="菜单类型无效",
    scenario="创建菜单时指定了无效的菜单类型",
)
