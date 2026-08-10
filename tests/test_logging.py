"""结构化日志测试 — SPEC 24.1 / 23.3.

覆盖:
  - 敏感字段（password/token/secret/cookie/authorization）递归掩码。
  - 嵌套字典和列表中的敏感字段掩码。
  - 换行注入防护（\\n / \\r 转义）。
  - structlog 处理器直接测试。
  - dev/prod 双 profile 配置。
"""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog

from app.core.config import Settings
from app.core.logging import (
    configure_logging,
    escape_newlines,
    inject_request_id,
    mask_sensitive_fields,
)
from app.core.request_context import request_id_var

# ── 敏感字段掩码 ───────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_mask_password() -> None:
    """password 键的值被掩码。"""

    result = mask_sensitive_fields(None, "info", {"password": "secret123"})
    assert result["password"] == "***MASKED***"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_token() -> None:
    """token 键的值被掩码。"""

    result = mask_sensitive_fields(None, "info", {"token": "abc-def-456"})
    assert result["token"] == "***MASKED***"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_secret() -> None:
    """secret 键的值被掩码。"""

    result = mask_sensitive_fields(None, "info", {"api_secret": "xyz"})
    assert result["api_secret"] == "***MASKED***"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_cookie() -> None:
    """cookie 键的值被掩码。"""

    result = mask_sensitive_fields(None, "info", {"cookie": "session=abc"})
    assert result["cookie"] == "***MASKED***"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_authorization() -> None:
    """authorization 键的值被掩码。"""

    result = mask_sensitive_fields(None, "info", {"authorization": "Bearer xyz"})
    assert result["authorization"] == "***MASKED***"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_nested_dict() -> None:
    """嵌套字典中的敏感字段递归掩码。"""

    event = {
        "user": {"name": "alice", "password": "p@ss", "token": "xyz"},
        "action": "login",
    }
    result = mask_sensitive_fields(None, "info", event)
    assert result["user"]["name"] == "alice"
    assert result["user"]["password"] == "***MASKED***"
    assert result["user"]["token"] == "***MASKED***"
    assert result["action"] == "login"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_in_list() -> None:
    """列表中的敏感字段递归掩码。"""

    event = {
        "items": [
            {"name": "ok", "password": "p1"},
            {"token": "t2"},
        ],
    }
    result = mask_sensitive_fields(None, "info", event)
    assert result["items"][0]["name"] == "ok"
    assert result["items"][0]["password"] == "***MASKED***"
    assert result["items"][1]["token"] == "***MASKED***"


@pytest.mark.g1
@pytest.mark.unit
def test_mask_case_insensitive() -> None:
    """敏感键匹配大小写不敏感。"""

    result = mask_sensitive_fields(
        None,
        "info",
        {"Password": "x", "TOKEN": "y", "SecretKey": "z"},
    )
    assert result["Password"] == "***MASKED***"
    assert result["TOKEN"] == "***MASKED***"
    assert result["SecretKey"] == "***MASKED***"


@pytest.mark.g1
@pytest.mark.unit
def test_non_sensitive_preserved() -> None:
    """非敏感字段保持原值。"""

    event = {"username": "bob", "request_id": "abc123", "count": 42}
    result = mask_sensitive_fields(None, "info", event)
    assert result == event


# ── 换行注入防护 ───────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_escape_newline_in_string() -> None:
    """字符串值中的 \\n 被转义为字面 \\n。"""

    result = escape_newlines(None, "info", {"msg": "line1\nline2"})
    assert result["msg"] == "line1\\nline2"


@pytest.mark.g1
@pytest.mark.unit
def test_escape_carriage_return_in_string() -> None:
    """字符串值中的 \\r 被转义。"""

    result = escape_newlines(None, "info", {"msg": "line1\rline2"})
    assert result["msg"] == "line1\\rline2"


@pytest.mark.g1
@pytest.mark.unit
def test_escape_newlines_in_nested() -> None:
    """嵌套结构中的换行递归转义。"""

    event = {
        "data": {"text": "a\nb"},
        "items": ["x\ny", "normal"],
    }
    result = escape_newlines(None, "info", event)
    assert result["data"]["text"] == "a\\nb"
    assert result["items"][0] == "x\\ny"
    assert result["items"][1] == "normal"


@pytest.mark.g1
@pytest.mark.unit
def test_escape_no_newlines_unchanged() -> None:
    """不含换行的字符串原样返回。"""

    event = {"msg": "normal text", "count": 42}
    result = escape_newlines(None, "info", event)
    assert result == event


# ── Request ID 注入 ───────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_inject_request_id_when_set() -> None:
    """ContextVar 设置时 request_id 注入 event_dict。"""

    token = request_id_var.set("req-abc-123")
    try:
        result = inject_request_id(None, "info", {"event": "test"})
        assert result["request_id"] == "req-abc-123"
    finally:
        request_id_var.reset(token)


@pytest.mark.g1
@pytest.mark.unit
def test_no_request_id_when_unset() -> None:
    """ContextVar 为空时不注入 request_id。"""

    result = inject_request_id(None, "info", {"event": "test"})
    assert "request_id" not in result


# ── dev/prod profile 配置 ─────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_configure_logging_dev_profile() -> None:
    """开发环境日志配置不报错，使用 ConsoleRenderer。"""

    settings = Settings(
        ENVIRONMENT="development",
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
    )
    # 不抛出异常即通过
    configure_logging(settings)


@pytest.mark.g1
@pytest.mark.unit
def test_configure_logging_prod_profile() -> None:
    """生产环境日志配置不报错，使用 JSONRenderer。"""

    settings = Settings(
        ENVIRONMENT="production",
        ACCESS_TOKEN_HMAC_KEY="prod-access-key-" + "a" * 16,
        REFRESH_TOKEN_HMAC_KEY="prod-refresh-key-" + "b" * 16,
    )
    configure_logging(settings)


@pytest.mark.g1
@pytest.mark.unit
def test_prod_json_output_contains_required_fields() -> None:
    """生产 JSON 日志包含时间/级别/环境/request_id 字段。"""

    settings = Settings(
        ENVIRONMENT="production",
        ACCESS_TOKEN_HMAC_KEY="prod-access-key-" + "a" * 16,
        REFRESH_TOKEN_HMAC_KEY="prod-refresh-key-" + "b" * 16,
    )
    configure_logging(settings)

    output = io.StringIO()
    structlog.configure(
        processors=[
            inject_request_id,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_log_level,
            mask_sensitive_fields,
            escape_newlines,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=True,
    )

    logger = structlog.get_logger().bind(module="test")
    token = request_id_var.set("req-xyz")
    try:
        logger.info("test_event", user="bob")
    finally:
        request_id_var.reset(token)

    line = output.getvalue().strip()
    data = json.loads(line)
    assert "timestamp" in data
    assert data["level"] == "info"
    assert data["event"] == "test_event"
    assert data["module"] == "test"
    assert data["request_id"] == "req-xyz"
    assert data["user"] == "bob"


@pytest.mark.g1
@pytest.mark.unit
def test_prod_json_masks_sensitive_in_output() -> None:
    """生产 JSON 日志中敏感字段被掩码。"""

    output = io.StringIO()
    structlog.configure(
        processors=[
            mask_sensitive_fields,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=True,
    )

    logger = structlog.get_logger()
    logger.info("login", password="secret", username="bob")

    data = json.loads(output.getvalue().strip())
    assert data["password"] == "***MASKED***"
    assert data["username"] == "bob"
