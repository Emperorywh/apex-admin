"""错误码注册表与格式校验（SPEC §10.2、§5.5）。

错误码全局唯一且稳定，按 ``<MODULE>.<REASON>`` 格式划分命名空间。
客户端业务判断只能使用稳定错误码，不得依赖展示文案或临时字符串（SPEC §10.2）。

模块前缀约定：
    - ``APP`` — 应用框架级通用错误（参数、认证、授权、不存在、冲突、系统）
    - ``DB``  — 数据库基础设施层错误（完整性约束、操作错误）
    - 业务模块使用自己的模块编码作为前缀（例如 ``USER.NOT_FOUND``）

RFC 9457 ``type`` 字段规则（SPEC §9.3）：
    - 业务错误码（非 ``APP`` / ``DB`` 前缀）→ ``urn:apex:problem:<小写错误码>``
    - 框架级错误码 → ``about:blank``
"""

from __future__ import annotations

import re

# 错误码格式正则：<MODULE>.<REASON>，只允许大写字母、数字和下划线（SPEC §5.5）
# MODULE 和 REASON 均以大写字母开头，不允许以数字或下划线开头
_ERROR_CODE_PATTERN: re.Pattern[str] = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$")

# 框架/基础设施模块前缀集合
# 这些前缀的错误码在 RFC 9457 响应中使用 about:blank 作为 type（SPEC §9.3）
# 业务模块使用自己的模块编码（如 USER、ROLE），不在此集合中
FRAMEWORK_MODULE_PREFIXES: frozenset[str] = frozenset({"APP", "DB"})


def is_valid_error_code(code: str) -> bool:
    """校验错误码格式是否符合 MODULE.REASON 约定（SPEC §5.5、§10.2）。

    合法格式：``<MODULE>.<REASON>``，MODULE 和 REASON 均以大写字母开头，
    只允许大写字母、数字和下划线。例如 ``USER.NOT_FOUND``、``APP.PARAMETER``。

    Args:
        code: 待校验的错误码字符串

    Returns:
        格式合法返回 True，否则 False
    """
    return bool(_ERROR_CODE_PATTERN.match(code))


def is_framework_code(code: str) -> bool:
    """判断错误码是否属于框架/基础设施层（非业务模块）。

    框架级错误码使用 ``APP`` 或 ``DB`` 前缀，在 RFC 9457 响应中
    ``type`` 固定为 ``about:blank``（SPEC §9.3）。
    业务模块的错误码（例如 ``USER.NOT_FOUND``）使用 ``urn:apex:problem:`` 前缀。

    Args:
        code: 错误码字符串

    Returns:
        属于框架/基础设施层返回 True，属于业务模块返回 False
    """
    module = code.split(".", 1)[0] if "." in code else code
    return module in FRAMEWORK_MODULE_PREFIXES


def build_problem_type(code: str) -> str:
    """根据错误码构建 RFC 9457 type URI（SPEC §9.3）。

    业务错误的 type 为 ``urn:apex:problem:<小写错误码>``，
    例如 ``urn:apex:problem:user.not_found``。
    框架级错误的 type 为 ``about:blank``。

    Args:
        code: 错误码字符串

    Returns:
        RFC 9457 type URI
    """
    if is_framework_code(code):
        return "about:blank"
    return f"urn:apex:problem:{code.lower()}"
