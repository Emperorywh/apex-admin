"""JSON 日志格式化器与敏感字段过滤（SPEC §24.1、§23.3）。

输出一行一个 JSON 对象，包含固定字段（timestamp、level、environment、module、message）
和可选字段（request_id 以及用户通过 ``extra=`` 传入的结构化字段）。

敏感字段（password、token、cookie、key、secret、authorization）的值在输出前被掩码，
消息文本中匹配敏感键值对模式的内容同样被掩码（SPEC §24.1、§23.3）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from app.middleware.request_id import get_request_id

# 敏感字段子串列表（不区分大小写匹配）
# SPEC §24.1：过滤密码、Token、Cookie、密钥和其他敏感字段
_SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "token",
    "cookie",
    "key",
    "secret",
    "authorization",
)

# 敏感值的统一掩码
_REDACTED = "***REDACTED***"

# 标准 LogRecord 属性名集合，用于区分用户通过 extra= 传入的自定义字段
_STANDARD_LOGRECORD_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)

# 消息中敏感键值对模式的正则，匹配 (key)(separator)(value) 三组
# key 部分匹配包含敏感子串的标识符（如 password、api_key、access_token）
_MESSAGE_REDACT_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)(\w*(?:" + "|".join(_SENSITIVE_SUBSTRINGS) + r")\w*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)


def _is_sensitive_key(name: str) -> bool:
    """判断字段名是否包含敏感子串。"""
    lowered = name.lower()
    return any(sub in lowered for sub in _SENSITIVE_SUBSTRINGS)


def _redact_message(message: str) -> str:
    """将消息文本中匹配敏感键值对模式的值替换为掩码。"""
    return _MESSAGE_REDACT_PATTERN.sub(r"\1\2" + _REDACTED, message)


def _redact_extra_fields(record: logging.LogRecord) -> dict[str, object]:
    """提取并过滤 LogRecord 中的 extra 字段。

    遍历 ``record.__dict__`` 中非标准属性，敏感字段的值替换为掩码。
    """
    extra: dict[str, object] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_LOGRECORD_ATTRS:
            continue
        if _is_sensitive_key(key):
            extra[key] = _REDACTED
        else:
            extra[key] = value
    return extra


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式化器（SPEC §24.1）。

    每条日志输出为一行 JSON 对象，包含以下固定字段：
    - timestamp: UTC ISO 8601 时间戳
    - level: 日志级别名称
    - environment: 运行环境名称
    - module: logger 名称
    - message: 日志消息（敏感值已掩码）

    可选字段：
    - request_id: 当前请求的 Request ID（来自 ContextVar）
    - exception: 异常堆栈文本（当记录异常时）
    - 其余 extra 字段（敏感字段已掩码）
    """

    def __init__(self, environment: str = "development") -> None:
        super().__init__()
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        """将 LogRecord 格式化为一行 JSON 字符串。"""
        # 构建固定字段
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "environment": self._environment,
            "module": record.name,
            "message": _redact_message(record.getMessage()),
        }

        # 关联 Request ID（来自 ContextVar，仅用于日志关联）
        request_id = get_request_id()
        if request_id is not None:
            entry["request_id"] = request_id

        # 合并 extra 字段（敏感字段已掩码）
        extra = _redact_extra_fields(record)
        if extra:
            entry.update(extra)

        # 异常堆栈（SPEC §24.1：异常日志包含完整内部堆栈）
        if record.exc_info is not None:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str)
