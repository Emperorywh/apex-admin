"""structlog 结构化日志配置.

SPEC 24.1 / 23.3:
  - 使用结构化日志，包含时间、级别、环境、模块和 Request ID。
  - 请求日志包含方法、路径、状态码和耗时。
  - 过滤密码、Token、Cookie、密钥等敏感字段（递归掩码）。
  - 日志内容防止换行注入。

SPEC 24.3:
  - 生产环境：单行 JSON 输出到标准输出。
  - 开发环境：彩色可读控制台渲染。

本模块通过 ``configure_logging(settings)`` 在应用创建时初始化。
不在模块导入阶段调用 ``structlog.configure``，避免副作用。
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from app.core.config import Environment, Settings
from app.core.request_context import request_id_var

# ── 敏感键片段 ────────────────────────────────────────────────────────────
#
# SPEC 24.1 / 23.3: 过滤 password、token、secret、cookie、authorization。
# 通过子串匹配（大小写不敏感）识别敏感键，递归掩码嵌套结构与列表。

_SENSITIVE_FRAGMENTS: frozenset[str] = frozenset(
    {
        "password",
        "token",
        "secret",
        "cookie",
        "authorization",
    },
)

# 掩码占位符，统一使用固定值避免泄漏长度信息。
_MASKED = "***MASKED***"


def _is_sensitive_key(key: str) -> bool:
    """判断键名是否属于敏感类别.

    使用子串匹配覆盖各种命名风格（snake_case、camelCase、kebab-case）。
    """

    key_lower = key.lower()
    return any(fragment in key_lower for fragment in _SENSITIVE_FRAGMENTS)


def _mask_recursive(key: str, value: Any) -> Any:
    """递归掩码敏感值.

    - 键名匹配敏感片段时，值替换为掩码占位符。
    - 字典递归处理每个键值对。
    - 列表/元组递归处理每个元素（沿用外层键名判断）。
    - 其他类型原样返回。
    """

    if _is_sensitive_key(key):
        return _MASKED
    if isinstance(value, dict):
        return {k: _mask_recursive(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_recursive(key, v) for v in value]
    if isinstance(value, tuple):
        return tuple(_mask_recursive(key, v) for v in value)
    return value


def mask_sensitive_fields(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog 处理器：递归掩码敏感字段.

    SPEC 23.3: 日志内容防止敏感信息泄露。
    遍历 event_dict 的每个键值对，对敏感键的值执行掩码。
    """

    return {k: _mask_recursive(k, v) for k, v in event_dict.items()}


def _escape_string(value: str) -> str:
    """转义字符串中的换行符，防止日志注入.

    SPEC 23.3: 日志内容防止换行注入。
    将 ``\\n`` 和 ``\\r`` 替换为字面转义序列，
    防止攻击者通过注入换行伪造日志条目。
    """

    return value.replace("\n", "\\n").replace("\r", "\\r")


def _escape_recursive(value: Any) -> Any:
    """递归转义字符串值中的换行符."""

    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, dict):
        return {k: _escape_recursive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_escape_recursive(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_escape_recursive(v) for v in value)
    return value


def escape_newlines(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog 处理器：转义日志值中的换行符防注入.

    SPEC 23.3: 日志内容防止换行注入。
    递归处理 event_dict 中所有字符串值。
    """

    return {k: _escape_recursive(v) for k, v in event_dict.items()}


def inject_request_id(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog 处理器：从 ContextVar 注入 Request ID.

    SPEC 24.1 / 9.5: Request ID 写入结构化日志。
    读取 ``request_id_var``（仅用于日志关联，SPEC 5.8），
    非空时添加到 event_dict。
    """

    rid = request_id_var.get()
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def configure_logging(settings: Settings) -> None:
    """配置 structlog 结构化日志.

    根据 ``settings.ENVIRONMENT`` 选择渲染器：
      - production: 单行 JSON（SPEC 24.3）。
      - development/testing: 彩色控制台渲染。

    处理器管线（按执行顺序）:
      1. inject_request_id — 从 ContextVar 注入 Request ID。
      2. add_environment — 注入运行环境标识。
      3. mask_sensitive_fields — 递归掩码敏感字段。
      4. escape_newlines — 转义换行防注入。
      5. TimeStamper — ISO 8601 UTC 时间戳。
      6. add_log_level — 注入日志级别。
      7. format_exc_info — 格式化异常堆栈。
      8. renderer — 按环境选择 ConsoleRenderer 或 JSONRenderer。
    """

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    def add_environment(
        logger: Any,
        method_name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """注入 environment 字段到每条日志。"""

        event_dict["environment"] = settings.ENVIRONMENT.value
        return event_dict

    shared_processors: list[Any] = [
        inject_request_id,
        add_environment,
        mask_sensitive_fields,
        escape_newlines,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
    ]

    if settings.ENVIRONMENT == Environment.PRODUCTION:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
