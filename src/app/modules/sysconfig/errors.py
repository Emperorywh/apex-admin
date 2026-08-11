"""系统配置模块错误码与异常 — SPEC 10.1 / 10.2 / 16.1 / 16.2.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间。
  - 错误码与展示文案分离。

模块错误码在导入时注册到框架默认注册表 ``default_registry``。
"""

from __future__ import annotations

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import (
    ConflictError,
    NotFoundError,
    ParameterError,
)

# ── 错误码常量 ──────────────────────────────────────────────────────────────

#: 配置项不存在 — 按 ID 查询或操作配置项但未找到。
SYSCONFIG_NOT_FOUND = "SYSCONFIG.NOT_FOUND"

#: 配置项已禁用 — 尝试禁用已处于禁用状态的配置项。
SYSCONFIG_ALREADY_DISABLED = "SYSCONFIG.ALREADY_DISABLED"

#: 配置项已启用 — 尝试启用已处于启用状态的配置项。
SYSCONFIG_ALREADY_ACTIVE = "SYSCONFIG.ALREADY_ACTIVE"

#: 配置键在分组内重复 — 同一分组下配置键必须唯一（SPEC 16.1）。
SYSCONFIG_DUPLICATE_KEY = "SYSCONFIG.DUPLICATE_KEY"

#: 配置值类型不合法 — 指定了不支持的配置值类型。
SYSCONFIG_INVALID_TYPE = "SYSCONFIG.INVALID_TYPE"

#: 配置值与声明类型不匹配 — 值无法按声明类型解析（SPEC 16.1）。
SYSCONFIG_VALUE_TYPE_MISMATCH = "SYSCONFIG.VALUE_TYPE_MISMATCH"

#: 核心安全配置不可被普通后台覆盖 — SPEC 16.1。
SYSCONFIG_CORE_SECURITY_PROTECTED = "SYSCONFIG.CORE_SECURITY_PROTECTED"

#: 越键读取 — 尝试读取模块未声明依赖的配置键（SPEC 16.2）。
SYSCONFIG_KEY_NOT_DECLARED = "SYSCONFIG.KEY_NOT_DECLARED"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class ConfigNotFoundError(NotFoundError):
    """配置项不存在 — HTTP 404."""

    code = SYSCONFIG_NOT_FOUND


class ConfigAlreadyDisabledError(ConflictError):
    """配置项已禁用 — HTTP 409."""

    code = SYSCONFIG_ALREADY_DISABLED


class ConfigAlreadyActiveError(ConflictError):
    """配置项已启用 — HTTP 409."""

    code = SYSCONFIG_ALREADY_ACTIVE


class ConfigDuplicateKeyError(ConflictError):
    """配置键在分组内重复 — HTTP 409.

    SPEC 16.1: "配置键全局唯一或在分组内唯一"。
    本模块决策：分组内唯一（文档化）。
    """

    code = SYSCONFIG_DUPLICATE_KEY


class InvalidConfigTypeError(ParameterError):
    """配置值类型不合法 — HTTP 400."""

    code = SYSCONFIG_INVALID_TYPE


class ConfigValueTypeMismatchError(ParameterError):
    """配置值与声明类型不匹配 — HTTP 400.

    SPEC 16.1: "配置值在保存时执行类型校验"。
    """

    code = SYSCONFIG_VALUE_TYPE_MISMATCH


class CoreSecurityConfigProtectedError(ConflictError):
    """核心安全配置不可被普通后台覆盖 — HTTP 409.

    SPEC 16.1: "核心安全配置不得由普通后台配置随意覆盖"。
    """

    code = SYSCONFIG_CORE_SECURITY_PROTECTED


class ConfigKeyNotDeclaredError(ParameterError):
    """越键读取 — 尝试读取模块未声明依赖的配置键 — HTTP 400.

    SPEC 16.2: "业务模块只读取自己声明依赖的配置"。
    SPEC 16.2: "不提供可以在任意位置随意读取任意键值的隐式全局配置对象"。
    """

    code = SYSCONFIG_KEY_NOT_DECLARED


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    SYSCONFIG_NOT_FOUND,
    404,
    meaning="配置项不存在",
    scenario="按 ID 查询或操作配置项但未找到",
)
default_registry.register(
    SYSCONFIG_ALREADY_DISABLED,
    409,
    meaning="配置项已禁用",
    scenario="尝试禁用已处于禁用状态的配置项",
)
default_registry.register(
    SYSCONFIG_ALREADY_ACTIVE,
    409,
    meaning="配置项已启用",
    scenario="尝试启用已处于启用状态的配置项",
)
default_registry.register(
    SYSCONFIG_DUPLICATE_KEY,
    409,
    meaning="配置键在分组内重复",
    scenario="同一分组下创建已存在的配置键（SPEC 16.1 分组内唯一）",
)
default_registry.register(
    SYSCONFIG_INVALID_TYPE,
    400,
    meaning="配置值类型不合法",
    scenario="指定了不支持的配置值类型（仅允许 string/int/bool/json）",
)
default_registry.register(
    SYSCONFIG_VALUE_TYPE_MISMATCH,
    400,
    meaning="配置值与声明类型不匹配",
    scenario="配置值无法按声明类型解析（SPEC 16.1 保存时类型校验）",
)
default_registry.register(
    SYSCONFIG_CORE_SECURITY_PROTECTED,
    409,
    meaning="核心安全配置不可被普通后台覆盖",
    scenario="尝试通过普通后台配置 API 修改标记为核心安全的配置（SPEC 16.1）",
)
default_registry.register(
    SYSCONFIG_KEY_NOT_DECLARED,
    400,
    meaning="越键读取",
    scenario="尝试读取模块未声明依赖的配置键（SPEC 16.2 声明式白名单）",
)
