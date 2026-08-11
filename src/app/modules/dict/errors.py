"""数据字典模块错误码与异常 — SPEC 10.1 / 10.2 / 17.1 / 17.2.

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
)

# ── 错误码常量 ──────────────────────────────────────────────────────────────

#: 字典类型不存在 — 按 ID 查询或操作字典类型但未找到。
DICT_TYPE_NOT_FOUND = "DICT.TYPE_NOT_FOUND"

#: 字典编码重复 — 同一编码已存在（SPEC 17.1: 字典编码保持稳定和唯一）。
DICT_TYPE_DUPLICATE_CODE = "DICT.TYPE_DUPLICATE_CODE"

#: 字典类型已禁用 — 尝试禁用已处于禁用状态的字典类型。
DICT_TYPE_ALREADY_DISABLED = "DICT.TYPE_ALREADY_DISABLED"

#: 字典类型已启用 — 尝试启用已处于启用状态的字典类型。
DICT_TYPE_ALREADY_ACTIVE = "DICT.TYPE_ALREADY_ACTIVE"

#: 字典类型被业务引用 — 删除被拒绝（SPEC 17.1: 删除保护）。
DICT_TYPE_REFERENCED = "DICT.TYPE_REFERENCED"

#: 字典项不存在 — 按 ID 查询或操作字典项但未找到。
DICT_ITEM_NOT_FOUND = "DICT.ITEM_NOT_FOUND"

#: 字典项稳定值在同类内重复 — 同一字典类型内稳定值必须唯一。
DICT_ITEM_DUPLICATE_VALUE = "DICT.ITEM_DUPLICATE_VALUE"

#: 字典项已禁用 — 尝试禁用已处于禁用状态的字典项。
DICT_ITEM_ALREADY_DISABLED = "DICT.ITEM_ALREADY_DISABLED"

#: 字典项已启用 — 尝试启用已处于启用状态的字典项。
DICT_ITEM_ALREADY_ACTIVE = "DICT.ITEM_ALREADY_ACTIVE"

#: 字典项所属字典类型已禁用 — 操作项时所属类型不可用。
DICT_TYPE_DISABLED = "DICT.TYPE_DISABLED"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class DictTypeNotFoundError(NotFoundError):
    """字典类型不存在 — HTTP 404."""

    code = DICT_TYPE_NOT_FOUND


class DictTypeDuplicateCodeError(ConflictError):
    """字典编码重复 — HTTP 409.

    SPEC 17.1: "字典编码保持稳定和唯一"。
    返回稳定冲突错误码 ``DICT.TYPE_DUPLICATE_CODE``。
    """

    code = DICT_TYPE_DUPLICATE_CODE


class DictTypeAlreadyDisabledError(ConflictError):
    """字典类型已禁用 — HTTP 409."""

    code = DICT_TYPE_ALREADY_DISABLED


class DictTypeAlreadyActiveError(ConflictError):
    """字典类型已启用 — HTTP 409."""

    code = DICT_TYPE_ALREADY_ACTIVE


class DictTypeReferencedError(ConflictError):
    """字典类型被业务引用，删除被拒绝 — HTTP 409.

    SPEC 17.1: "已被业务引用的字典类型具有删除保护"。
    """

    code = DICT_TYPE_REFERENCED


class DictItemNotFoundError(NotFoundError):
    """字典项不存在 — HTTP 404."""

    code = DICT_ITEM_NOT_FOUND


class DictItemDuplicateValueError(ConflictError):
    """字典项稳定值在同类内重复 — HTTP 409.

    SPEC 17.2: 字典项稳定值在同一字典类型内唯一。
    """

    code = DICT_ITEM_DUPLICATE_VALUE


class DictItemAlreadyDisabledError(ConflictError):
    """字典项已禁用 — HTTP 409."""

    code = DICT_ITEM_ALREADY_DISABLED


class DictItemAlreadyActiveError(ConflictError):
    """字典项已启用 — HTTP 409."""

    code = DICT_ITEM_ALREADY_ACTIVE


class DictTypeDisabledError(ConflictError):
    """字典类型已禁用，不可操作其字典项 — HTTP 409.

    SPEC 17.1: 字典类型禁用后，不可在其下创建或修改字典项。
    """

    code = DICT_TYPE_DISABLED


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    DICT_TYPE_NOT_FOUND,
    404,
    meaning="字典类型不存在",
    scenario="按 ID 查询或操作字典类型但未找到",
)
default_registry.register(
    DICT_TYPE_DUPLICATE_CODE,
    409,
    meaning="字典编码重复",
    scenario="创建或更新时使用了已存在的字典编码（SPEC 17.1 唯一性）",
)
default_registry.register(
    DICT_TYPE_ALREADY_DISABLED,
    409,
    meaning="字典类型已禁用",
    scenario="尝试禁用已处于禁用状态的字典类型",
)
default_registry.register(
    DICT_TYPE_ALREADY_ACTIVE,
    409,
    meaning="字典类型已启用",
    scenario="尝试启用已处于启用状态的字典类型",
)
default_registry.register(
    DICT_TYPE_REFERENCED,
    409,
    meaning="字典类型被业务引用，删除被拒绝",
    scenario="尝试删除被引用登记 Port 标记为业务引用的字典类型（SPEC 17.1 删除保护）",
)
default_registry.register(
    DICT_ITEM_NOT_FOUND,
    404,
    meaning="字典项不存在",
    scenario="按 ID 查询或操作字典项但未找到",
)
default_registry.register(
    DICT_ITEM_DUPLICATE_VALUE,
    409,
    meaning="字典项稳定值在同类内重复",
    scenario="创建或更新时在同一字典类型内使用了已存在的稳定值",
)
default_registry.register(
    DICT_ITEM_ALREADY_DISABLED,
    409,
    meaning="字典项已禁用",
    scenario="尝试禁用已处于禁用状态的字典项",
)
default_registry.register(
    DICT_ITEM_ALREADY_ACTIVE,
    409,
    meaning="字典项已启用",
    scenario="尝试启用已处于启用状态的字典项",
)
default_registry.register(
    DICT_TYPE_DISABLED,
    409,
    meaning="字典类型已禁用",
    scenario="尝试在已禁用的字典类型下创建或修改字典项",
)
