"""结构化 JSON 日志单元测试（SPEC §24.1、§9.5）。

覆盖验收条件：
- 结构化日志输出 JSON，含时间戳、级别、环境、模块、Request ID
- Request ID 每请求生成，写入响应头 X-Request-ID 和日志
- 敏感字段（password、token、cookie、key）从日志中过滤
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from io import StringIO
from typing import Any

import pytest

from app.logging.formatter import JsonFormatter
from app.middleware.request_id import request_id_var

pytestmark = [pytest.mark.unit, pytest.mark.g1]


@pytest.fixture
def json_logger() -> tuple[logging.Logger, StringIO]:
    """创建带 JSON 格式化器的 logger 和输出捕获流。

    返回 (logger, stream)，测试中从 stream 解析 JSON 输出。
    """
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(environment="testing"))
    logger = logging.getLogger(f"test.logging.{id(stream)}")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    # 防止传播到 root logger 导致重复输出
    logger.propagate = False
    return logger, stream


def _parse_output(stream: StringIO) -> dict[str, Any]:
    """解析 stream 中最后一行 JSON 输出。"""
    lines = [line for line in stream.getvalue().strip().splitlines() if line.strip()]
    assert lines, "日志输出为空"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# JSON 格式固定字段（验收条件：日志含时间戳、级别、环境、模块）
# ---------------------------------------------------------------------------


class TestJsonLogFormat:
    """验证 JSON 日志包含全部必需固定字段。"""

    def test_has_required_fields(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """日志输出包含 timestamp、level、environment、module、message。"""
        logger, stream = json_logger
        logger.info("测试消息")
        output = _parse_output(stream)

        assert "timestamp" in output
        assert output["level"] == "INFO"
        assert output["environment"] == "testing"
        assert "module" in output
        assert output["message"] == "测试消息"

    def test_level_reflects_log_level(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """日志 level 字段对应实际日志级别。"""
        logger, stream = json_logger
        logger.warning("警告消息")
        output = _parse_output(stream)
        assert output["level"] == "WARNING"

    def test_module_is_logger_name(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """module 字段等于 logger 名称。"""
        logger, stream = json_logger
        logger.info("test")
        output = _parse_output(stream)
        assert output["module"] == logger.name

    def test_timestamp_is_utc_iso8601(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """时间戳为 UTC ISO 8601 格式（以 +00:00 结尾）。"""
        logger, stream = json_logger
        logger.info("test")
        output = _parse_output(stream)
        ts = output["timestamp"]
        assert ts.endswith("+00:00")
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None

    def test_output_is_single_json_line(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """每条日志输出为一行 JSON 对象（SPEC §24.3）。"""
        logger, stream = json_logger
        logger.info("第一条")
        logger.info("第二条")
        lines = [line for line in stream.getvalue().strip().splitlines() if line.strip()]
        assert len(lines) == 2
        # 每行都可以独立解析为 JSON
        for line in lines:
            json.loads(line)

    def test_environment_from_formatter(self) -> None:
        """不同 environment 参数产生不同的 environment 字段值。"""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter(environment="production"))
        logger = logging.getLogger(f"test.logging.prod.{id(stream)}")
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.info("prod message")
        output = _parse_output(stream)
        assert output["environment"] == "production"


# ---------------------------------------------------------------------------
# Request ID 关联（验收条件：Request ID 写入日志）
# ---------------------------------------------------------------------------


class TestRequestIdCorrelation:
    """验证 Request ID 通过 ContextVar 关联到日志。"""

    def test_request_id_in_log(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """设置 ContextVar 后，日志包含 request_id 字段。"""
        logger, stream = json_logger
        token = request_id_var.set("req-abc-123")
        try:
            logger.info("processing request")
        finally:
            request_id_var.reset(token)
        output = _parse_output(stream)
        assert output["request_id"] == "req-abc-123"

    def test_no_request_id_when_not_set(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """未设置 ContextVar 时，日志不含 request_id 字段。"""
        logger, stream = json_logger
        # 确保未设置（默认为 None）
        assert request_id_var.get() is None
        logger.info("no request context")
        output = _parse_output(stream)
        assert "request_id" not in output

    def test_different_request_ids_in_sequence(
        self, json_logger: tuple[logging.Logger, StringIO]
    ) -> None:
        """不同请求上下文产生不同 request_id 日志条目。"""
        logger, stream = json_logger

        token1 = request_id_var.set("req-001")
        logger.info("first")
        request_id_var.reset(token1)

        token2 = request_id_var.set("req-002")
        logger.info("second")
        request_id_var.reset(token2)

        lines = [line for line in stream.getvalue().strip().splitlines() if line.strip()]
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["request_id"] == "req-001"
        assert second["request_id"] == "req-002"


# ---------------------------------------------------------------------------
# 敏感字段过滤（验收条件：password、token、cookie、key 从日志中过滤）
# ---------------------------------------------------------------------------


class TestSensitiveFieldFiltering:
    """验证敏感字段在日志输出中被掩码。"""

    def test_password_in_extra_filtered(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """extra 中的 password 字段被掩码。"""
        logger, stream = json_logger
        logger.info("login attempt", extra={"password": "secret123"})
        output = _parse_output(stream)
        assert output["password"] != "secret123"
        assert "***" in output["password"]

    def test_token_in_extra_filtered(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """extra 中的 token 字段被掩码。"""
        logger, stream = json_logger
        logger.info("auth", extra={"token": "bearer-abc123"})
        output = _parse_output(stream)
        assert output["token"] != "bearer-abc123"
        assert "***" in output["token"]

    def test_cookie_in_extra_filtered(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """extra 中的 cookie 字段被掩码。"""
        logger, stream = json_logger
        logger.info("request", extra={"cookie": "session=xyz789"})
        output = _parse_output(stream)
        assert output["cookie"] != "session=xyz789"
        assert "***" in output["cookie"]

    def test_key_in_extra_filtered(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """extra 中包含 key 子串的字段（如 api_key）被掩码。"""
        logger, stream = json_logger
        logger.info("config", extra={"api_key": "sk-12345"})
        output = _parse_output(stream)
        assert output["api_key"] != "sk-12345"
        assert "***" in output["api_key"]

    def test_secret_in_extra_filtered(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """extra 中包含 secret 子串的字段被掩码。"""
        logger, stream = json_logger
        logger.info("setup", extra={"client_secret": "super-secret-value"})
        output = _parse_output(stream)
        assert output["client_secret"] != "super-secret-value"
        assert "***" in output["client_secret"]

    def test_password_in_message_filtered(
        self, json_logger: tuple[logging.Logger, StringIO]
    ) -> None:
        """消息文本中 password=xxx 模式的值被掩码。"""
        logger, stream = json_logger
        logger.info("user login password=secret123")
        output = _parse_output(stream)
        assert "secret123" not in output["message"]
        assert "***" in output["message"]

    def test_token_in_message_filtered(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """消息文本中 token:xxx 模式的值被掩码。"""
        logger, stream = json_logger
        logger.info("auth token=abc-token-value failed")
        output = _parse_output(stream)
        assert "abc-token-value" not in output["message"]
        assert "***" in output["message"]

    def test_non_sensitive_field_preserved(
        self, json_logger: tuple[logging.Logger, StringIO]
    ) -> None:
        """非敏感 extra 字段保持原值不被过滤。"""
        logger, stream = json_logger
        logger.info("user action", extra={"user_id": "12345", "action": "read"})
        output = _parse_output(stream)
        assert output["user_id"] == "12345"
        assert output["action"] == "read"

    def test_non_sensitive_message_preserved(
        self, json_logger: tuple[logging.Logger, StringIO]
    ) -> None:
        """不含敏感信息的消息文本保持原样。"""
        logger, stream = json_logger
        logger.info("user 12345 performed read action")
        output = _parse_output(stream)
        assert output["message"] == "user 12345 performed read action"


# ---------------------------------------------------------------------------
# 异常日志（SPEC §24.1：异常日志包含完整内部堆栈）
# ---------------------------------------------------------------------------


class TestExceptionLogging:
    """验证异常日志包含堆栈信息。"""

    def test_exception_included(self, json_logger: tuple[logging.Logger, StringIO]) -> None:
        """记录异常时 JSON 输出包含 exception 字段。"""
        logger, stream = json_logger
        try:
            msg = "test error"
            raise ValueError(msg)
        except ValueError:
            logger.exception("操作失败")
        output = _parse_output(stream)
        assert "exception" in output
        assert "ValueError" in output["exception"]
        assert "test error" in output["exception"]
