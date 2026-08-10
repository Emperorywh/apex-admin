"""错误码注册表 — SPEC 10.2.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间，
    只允许大写字母、数字和下划线（SPEC 5.5）。
  - 错误码与展示文案分离。
  - 每个错误码具有含义、HTTP 状态码和适用场景。

注册表职责:
  1. 校验错误码格式（拒绝非法格式）。
  2. 保证全局唯一性（拒绝重复注册）。
  3. 存储每个错误码的元数据（含义、HTTP 状态码、适用场景）。

展示文案（``title``/``detail``）不存储在注册表中，由 API 边界的
异常处理器根据错误码和异常实例动态生成，实现文案与码分离
（SPEC 10.2: "错误码与展示文案分离"）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

# 错误码格式：<MODULE>.<REASON>，仅大写字母、数字和下划线（SPEC 5.5）。
# MODULE 和 REASON 均以大写字母开头，可含大写字母、数字和下划线。
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$")


class DuplicateErrorCodeError(Exception):
    """重复注册错误码时抛出.

    SPEC 10.2: "错误码全局唯一"。
    """


class InvalidErrorCodeFormatError(Exception):
    """错误码格式非法时抛出.

    SPEC 5.5: "业务错误码固定为 ``<MODULE>.<REASON>``，
    只允许大写字母、数字和下划线"。
    """


@dataclass(frozen=True)
class ErrorCodeMetadata:
    """错误码元数据 — 含义、HTTP 状态码和适用场景（SPEC 10.2）.

    属性:
        code:        稳定错误码，如 ``DB.UNIQUE_VIOLATION``。
        http_status: 对应的 HTTP 状态码。
        meaning:     错误码含义（开发者面向文档，非终端用户展示文案）。
        scenario:    适用场景描述（何时使用此错误码）。
    """

    code: str
    http_status: int
    meaning: str
    scenario: str


class ErrorCodeRegistry:
    """错误码注册表 — 格式校验、全局唯一和元数据管理.

    SPEC 10.2: "错误码全局唯一且稳定"、"每个错误码具有含义、
    HTTP 状态码和适用场景"、"错误码与展示文案分离"。

    使用方式::

        registry = ErrorCodeRegistry()
        registry.register(
            "USER.NOT_FOUND", 404,
            meaning="用户不存在",
            scenario="按 ID 查询用户但未找到时使用",
        )
        metadata = registry.get("USER.NOT_FOUND")

    元数据中的 ``meaning`` 和 ``scenario`` 是开发者面向的文档，
    不是面向终端用户的展示文案。终端用户看到的 ``title`` 和 ``detail``
    由 API 边界的异常处理器动态生成。
    """

    def __init__(self) -> None:
        """初始化空注册表."""

        self._codes: dict[str, ErrorCodeMetadata] = {}

    def register(
        self,
        code: str,
        http_status: int,
        *,
        meaning: str,
        scenario: str,
    ) -> None:
        """注册错误码及其元数据.

        参数:
            code:        错误码，格式 ``<MODULE>.<REASON>``。
            http_status: 对应 HTTP 状态码。
            meaning:     错误码含义。
            scenario:    适用场景。

        抛出:
            InvalidErrorCodeFormatError: 格式不合法。
            DuplicateErrorCodeError:     错误码已注册。
        """

        self._validate_format(code)
        if code in self._codes:
            raise DuplicateErrorCodeError(
                f"错误码已注册: {code}",
            )
        self._codes[code] = ErrorCodeMetadata(
            code=code,
            http_status=http_status,
            meaning=meaning,
            scenario=scenario,
        )

    def get(self, code: str) -> ErrorCodeMetadata | None:
        """获取错误码元数据，不存在返回 ``None``."""

        return self._codes.get(code)

    def __contains__(self, code: object) -> bool:
        return code in self._codes

    def __len__(self) -> int:
        return len(self._codes)

    @property
    def codes(self) -> MappingProxyType[str, ErrorCodeMetadata]:
        """返回已注册错误码的只读视图."""

        return MappingProxyType(self._codes)

    @staticmethod
    def _validate_format(code: str) -> None:
        """校验错误码格式 — SPEC 5.5 ``<MODULE>.<REASON>``."""

        if not isinstance(code, str) or not _ERROR_CODE_PATTERN.match(code):
            raise InvalidErrorCodeFormatError(
                f"错误码格式非法: {code!r}，"
                f"应为 <MODULE>.<REASON>，仅大写字母、数字和下划线",
            )


# ── 框架级错误码定义 ──────────────────────────────────────────────────────
#
# 框架级（非业务模块）的错误码。业务模块的错误码由各自模块通过
# ModuleDefinition 注册（SPEC 5.5，TASK-008 示例模块）。
# 注册表拒绝重复注册和非法格式（SPEC 10.2）。
#
# 元数据结构: (code, http_status, meaning, scenario)

_FRAMEWORK_ERROR_CODES: list[tuple[str, int, str, str]] = [
    (
        "APPLICATION.ERROR",
        500,
        "应用层未分类错误",
        "应用层异常未被更具体的子类捕获时的兜底",
    ),
    (
        "PARAMETER.INVALID",
        400,
        "请求参数不合法",
        "客户端请求参数格式或基本值不符合要求（非字段级校验）",
    ),
    (
        "VALIDATION.FAILED",
        422,
        "字段校验失败",
        "请求体字段未通过校验规则（Pydantic 或应用层校验）",
    ),
    (
        "AUTH.UNAUTHENTICATED",
        401,
        "未认证",
        "请求缺少有效的身份认证凭证",
    ),
    (
        "AUTH.FORBIDDEN",
        403,
        "无权限",
        "已认证用户无权执行所请求的操作",
    ),
    (
        "COMMON.NOT_FOUND",
        404,
        "资源不存在",
        "请求的资源标识在系统中不存在",
    ),
    (
        "COMMON.CONFLICT",
        409,
        "状态冲突",
        "请求与当前资源状态冲突，无法完成操作",
    ),
    (
        "SYSTEM.INTERNAL",
        500,
        "系统内部错误",
        "未处理的内部异常或系统故障",
    ),
    (
        "DB.UNIQUE_VIOLATION",
        409,
        "唯一约束冲突",
        "INSERT 或 UPDATE 违反数据库唯一约束（SPEC 8.4）",
    ),
    (
        "DB.CONNECTION_ERROR",
        503,
        "数据库连接错误",
        "无法连接数据库或连接中断（SPEC 8.1）",
    ),
]


def register_framework_error_codes(registry: ErrorCodeRegistry) -> None:
    """将框架级错误码注册到指定注册表.

    此函数注册非业务模块的框架错误码。业务模块的错误码由各自模块
    通过 ``ModuleDefinition`` 在应用启动时注册（SPEC 5.5）。
    """

    for code, http_status, meaning, scenario in _FRAMEWORK_ERROR_CODES:
        registry.register(
            code,
            http_status,
            meaning=meaning,
            scenario=scenario,
        )


# 模块级默认注册表 — 导入时自动注册框架级错误码。
# 全局单例，供异常处理器和模块注册使用。
default_registry = ErrorCodeRegistry()
register_framework_error_codes(default_registry)
