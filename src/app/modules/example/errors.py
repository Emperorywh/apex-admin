"""示例模块错误码与异常 — SPEC 10.1 / 10.2.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间。
  - 错误码与展示文案分离。
  - 每个错误码具有含义、HTTP 状态码和适用场景。

模块错误码在导入时注册到框架默认注册表 ``default_registry``，
使 API 边界的异常处理器能查找对应的 HTTP 状态码和含义元数据
（SPEC 10.1: "在 API 边界统一完成异常到 HTTP 响应的转换"）。
同一错误码也在 ``ModuleDefinition`` 中声明，供 ``modules validate``
检测全局重复（SPEC 5.5）。
"""

from __future__ import annotations

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import ConflictError, NotFoundError

# ── 错误码常量 ──────────────────────────────────────────────────────────────
#
# SPEC 5.5: "业务错误码固定为 ``<MODULE>.<REASON>``，
# 只允许大写字母、数字和下划线"。

EXAMPLE_NOT_FOUND = "EXAMPLE.NOT_FOUND"
EXAMPLE_CONFLICT = "EXAMPLE.CONFLICT"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class ExampleItemNotFoundError(NotFoundError):
    """示例条目不存在 — HTTP 404.

    按 ID 查询或操作条目但未找到时使用。
    继承 ``NotFoundError``（HTTP 404），覆写错误码为模块专属编码。
    """

    code = EXAMPLE_NOT_FOUND


class ExampleItemConflictError(ConflictError):
    """示例条目名称冲突 — HTTP 409.

    创建或更新条目时名称已被其他条目占用。
    继承 ``ConflictError``（HTTP 409），覆写错误码为模块专属编码。
    """

    code = EXAMPLE_CONFLICT


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────
#
# 导入此模块时自动注册。注册表拒绝重复注册和非法格式（SPEC 10.2）。
# composition/modules.py 导入 definition.py → definition.py 导入 errors.py
# 从而在应用启动前完成注册。

default_registry.register(
    EXAMPLE_NOT_FOUND,
    404,
    meaning="示例条目不存在",
    scenario="按 ID 查询示例条目但未找到时使用",
)
default_registry.register(
    EXAMPLE_CONFLICT,
    409,
    meaning="示例条目名称冲突",
    scenario="创建或更新示例条目时名称已被占用",
)
