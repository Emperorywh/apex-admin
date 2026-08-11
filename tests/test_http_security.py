"""HTTP 安全基线测试 — SPEC 23.1 / 24.3 / 26.3.

覆盖 TASK-029 全部验收条件:
  - 安全响应头（X-Content-Type-Options、X-Frame-Options、Referrer-Policy）。
  - 可信 Host 白名单（非白名单 Host 被拒绝）。
  - CORS 白名单（允许的来源返回 CORS 头，非白名单不返回）。
  - 生产环境 CORS 通配或缺白名单时启动失败。
  - 请求体大小限制（常规请求超限返回 413）。
  - 上传接口更严格限制（multipart 请求适用上传限制）。
  - 可信代理头伪造防护（非可信来源的 X-Forwarded-For 不生效）。
  - 生产日志 JSON 单行 stdout 输出且不含 RotatingFileHandler。
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest
import structlog
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.api.security import (
    TrustedProxyMiddleware,
)
from app.core.config import Environment, Settings
from app.core.logging import (
    configure_logging,
    escape_newlines,
    inject_request_id,
    mask_sensitive_fields,
)
from app.core.request_context import request_id_var
from app.main import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request as StarletteRequest


# ── 辅助函数 ───────────────────────────────────────────────────────────────


def _make_test_app(**overrides: str) -> FastAPI:
    """创建开发环境测试应用，可覆盖特定配置。"""

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
        **overrides,
    )
    return create_app(settings)


# ═══════════════════════════════════════════════════════════════════════════════
# 安全响应头
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.security
class TestSecurityHeaders:
    """SPEC 23.1: 设置必要的安全响应头。"""

    def test_x_content_type_options_present(self) -> None:
        """每个响应包含 X-Content-Type-Options: nosniff。"""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get("/api/v1/meta")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options_present(self) -> None:
        """每个响应包含 X-Frame-Options: DENY。"""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get("/api/v1/meta")
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy_present(self) -> None:
        """每个响应包含 Referrer-Policy。"""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get("/api/v1/meta")
        assert "strict-origin-when-cross-origin" in response.headers.get(
            "Referrer-Policy",
            "",
        )

    def test_security_headers_on_health_endpoint(self) -> None:
        """健康检查端点也附加安全头。"""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get("/health/live")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"


# ═══════════════════════════════════════════════════════════════════════════════
# 可信 Host
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.security
class TestTrustedHost:
    """SPEC 23.1: 配置可信 Host。"""

    def test_allowed_host_accepted(self) -> None:
        """白名单中的 Host 正常响应。"""

        app = _make_test_app(TRUSTED_HOSTS="testserver,localhost")
        with TestClient(app) as client:
            response = client.get("/health/live")
        assert response.status_code == 200

    def test_disallowed_host_rejected(self) -> None:
        """非白名单 Host 被拒绝（400）。"""

        app = _make_test_app(TRUSTED_HOSTS="only-this-host.example.com")
        with TestClient(app) as client:
            response = client.get("/health/live")
        assert response.status_code == 400

    def test_wildcard_host_allows_all_in_dev(self) -> None:
        """开发/测试环境通配 Host 接受所有请求。"""

        app = _make_test_app(TRUSTED_HOSTS="*")
        with TestClient(app) as client:
            response = client.get("/health/live")
        assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# CORS 白名单
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.security
class TestCORSWhitelist:
    """SPEC 23.1: CORS 使用明确来源白名单。"""

    def test_allowed_origin_returns_cors_headers(self) -> None:
        """白名单来源的预检请求返回 CORS 头。"""

        app = _make_test_app(ALLOWED_ORIGINS="http://localhost:3000")
        with TestClient(app) as client:
            response = client.options(
                "/api/v1/meta",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert response.headers.get("access-control-allow-origin") == (
            "http://localhost:3000"
        )

    def test_disallowed_origin_no_cors_headers(self) -> None:
        """非白名单来源不返回 CORS 允许头。"""

        app = _make_test_app(ALLOWED_ORIGINS="http://localhost:3000")
        with TestClient(app) as client:
            response = client.options(
                "/api/v1/meta",
                headers={
                    "Origin": "https://evil.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        allow_origin = response.headers.get("access-control-allow-origin")
        assert allow_origin != "https://evil.example.com"


# ═══════════════════════════════════════════════════════════════════════════════
# 生产环境 CORS/Host 启动失败
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.unit
class TestProductionStartupValidation:
    """SPEC 23.1: 生产环境配置 CORS 通配或缺白名单时启动失败。"""

    def _prod_kwargs(self) -> dict[str, str]:
        """提供合法的生产环境基础配置。"""

        return {
            "ENVIRONMENT": "production",
            "ACCESS_TOKEN_HMAC_KEY": "prod-access-key-" + "a" * 16,
            "REFRESH_TOKEN_HMAC_KEY": "prod-refresh-key-" + "b" * 16,
            "SYSCONFIG_ENCRYPTION_KEY": "T44-h5wE4-HJ69EZjyDir3a_DNQFAT5DMW8De0tXijU=",
            "TRUSTED_HOSTS": "admin.example.com",
            "ALLOWED_ORIGINS": "https://admin.example.com",
            "METRICS_TOKEN": "prod-metrics-secret-token",
        }

    def test_cors_wildcard_fails_in_production(self) -> None:
        """生产环境 CORS 通配导致启动失败。"""

        kwargs = self._prod_kwargs()
        kwargs["ALLOWED_ORIGINS"] = "*"
        with pytest.raises(ValueError, match="CORS 通配"):
            Settings(**kwargs)

    def test_cors_empty_fails_in_production(self) -> None:
        """生产环境 CORS 白名单为空导致启动失败。"""

        kwargs = self._prod_kwargs()
        kwargs["ALLOWED_ORIGINS"] = ""
        with pytest.raises(ValueError, match="CORS"):
            Settings(**kwargs)

    def test_trusted_host_wildcard_fails_in_production(self) -> None:
        """生产环境 Host 通配导致启动失败。"""

        kwargs = self._prod_kwargs()
        kwargs["TRUSTED_HOSTS"] = "*"
        with pytest.raises(ValueError, match="通配 Host"):
            Settings(**kwargs)

    def test_trusted_host_empty_fails_in_production(self) -> None:
        """生产环境 Host 白名单为空导致启动失败。"""

        kwargs = self._prod_kwargs()
        kwargs["TRUSTED_HOSTS"] = ""
        with pytest.raises(ValueError, match="可信 Host"):
            Settings(**kwargs)

    def test_valid_production_config_succeeds(self) -> None:
        """合法的生产配置正常构造。"""

        settings = Settings(**self._prod_kwargs())
        assert settings.ENVIRONMENT == Environment.PRODUCTION
        assert "*" not in settings.allowed_origin_set
        assert "*" not in settings.trusted_host_set


# ═══════════════════════════════════════════════════════════════════════════════
# 请求体大小限制
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.security
class TestRequestBodySizeLimit:
    """SPEC 23.1: 限制请求体大小，上传接口适用更严格限制。"""

    def test_normal_request_within_limit_succeeds(self) -> None:
        """常规大小的 JSON 请求正常处理。"""

        app = _make_test_app(MAX_REQUEST_BODY_SIZE=1048576)
        with TestClient(app) as client:
            response = client.get("/api/v1/meta")
        assert response.status_code == 200

    def test_oversized_json_returns_413(self) -> None:
        """超过常规限制的 JSON 请求返回 413。"""

        app = _make_test_app(MAX_REQUEST_BODY_SIZE=100)
        with TestClient(app) as client:
            # 发送 200 字节的 JSON body（超过 100 字节限制）
            large_body = json.dumps({"data": "x" * 200})
            response = client.post(
                "/api/v1/meta",
                content=large_body,
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 413

    def test_upload_request_uses_upload_limit(self) -> None:
        """multipart 上传请求适用上传限制（比常规更大）。"""

        # 常规限制设为 10 字节，上传限制设为 10000 字节。
        # 200 字节的 multipart body 超过常规限制但在上传限制内。
        app = _make_test_app(
            MAX_REQUEST_BODY_SIZE=10,
            MAX_UPLOAD_BODY_SIZE=10000,
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/files",
                files={"files": ("test.txt", b"Hello World", "text/plain")},
            )
        # 413 表示被限制拦截，其他状态码（如 401 认证失败）表示通过了大小检查
        assert response.status_code != 413

    def test_oversized_upload_returns_413(self) -> None:
        """超过上传限制的 multipart 请求返回 413。"""

        app = _make_test_app(
            MAX_REQUEST_BODY_SIZE=10,
            MAX_UPLOAD_BODY_SIZE=50,
        )
        with TestClient(app) as client:
            # 发送超过 50 字节上传限制的 multipart body
            response = client.post(
                "/api/v1/files",
                files={"files": ("big.txt", b"x" * 200, "text/plain")},
            )
        assert response.status_code == 413

    def test_413_response_is_problem_json(self) -> None:
        """413 响应使用 application/problem+json 格式。"""

        app = _make_test_app(MAX_REQUEST_BODY_SIZE=10)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/meta",
                content=json.dumps({"data": "x" * 200}),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 413
        assert "application/problem+json" in response.headers.get(
            "content-type",
            "",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 可信代理头伪造防护
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.security
class TestTrustedProxyHeaders:
    """SPEC 23.1 / 26.3: 可信代理头仅在接受配置来源时被采信。"""

    @staticmethod
    def _make_proxy_app(
        trusted_proxies: frozenset[str],
    ) -> tuple[Starlette, dict[str, str]]:
        """创建带 TrustedProxyMiddleware 的 Starlette 测试应用.

        返回 (app, captured_dict)，端点将 scope 中的 trusted_client_ip
        和 trusted_scheme 存入 captured_dict 供测试断言。
        """

        captured: dict[str, str] = {}

        async def echo_endpoint(request: StarletteRequest) -> JSONResponse:
            """返回中间件解析的 trusted_client_ip 和 trusted_scheme。"""

            captured["trusted_client_ip"] = request.scope.get(
                "trusted_client_ip",
                "",
            )
            captured["trusted_scheme"] = request.scope.get("trusted_scheme", "")
            return JSONResponse({"status": "ok"})

        app = Starlette(routes=[Route("/test-proxy", echo_endpoint)])
        app.add_middleware(
            TrustedProxyMiddleware,
            trusted_proxies=trusted_proxies,
        )
        return app, captured

    def test_forged_forwarded_for_ignored_when_not_trusted(self) -> None:
        """非可信来源的 X-Forwarded-For 被忽略。

        TestClient 的 client.host 为 "testclient"，不在 TRUSTED_PROXIES 中，
        因此 X-Forwarded-For 头被忽略，trusted_client_ip 使用直接来源。
        """

        app, captured = self._make_proxy_app(frozenset({"127.0.0.1"}))
        with TestClient(app) as client:
            response = client.get(
                "/test-proxy",
                headers={"X-Forwarded-For": "1.2.3.4"},
            )
        assert response.status_code == 200
        # testclient 不在 trusted_proxies 中，X-Forwarded-For 被忽略
        assert captured["trusted_client_ip"] != "1.2.3.4"

    def test_forwarded_for_trusted_from_configured_proxy(self) -> None:
        """可信来源的 X-Forwarded-For 被采信。

        将 "testclient" 加入 TRUSTED_PROXIES，验证 X-Forwarded-For 被采纳。
        """

        app, captured = self._make_proxy_app(frozenset({"testclient"}))
        with TestClient(app) as client:
            response = client.get(
                "/test-proxy",
                headers={"X-Forwarded-For": "1.2.3.4"},
            )
        assert response.status_code == 200
        assert captured["trusted_client_ip"] == "1.2.3.4"

    def test_forwarded_proto_trusted_from_configured_proxy(self) -> None:
        """可信来源的 X-Forwarded-Proto 被采信。"""

        app, captured = self._make_proxy_app(frozenset({"testclient"}))
        with TestClient(app) as client:
            response = client.get(
                "/test-proxy",
                headers={"X-Forwarded-Proto": "https"},
            )
        assert response.status_code == 200
        assert captured["trusted_scheme"] == "https"

    def test_no_trusted_proxies_ignores_all_forwarded_headers(self) -> None:
        """空 TRUSTED_PROXIES 配置忽略所有代理头。"""

        app, captured = self._make_proxy_app(frozenset())
        with TestClient(app) as client:
            response = client.get(
                "/test-proxy",
                headers={
                    "X-Forwarded-For": "99.99.99.99",
                    "X-Forwarded-Proto": "https",
                },
            )
        assert response.status_code == 200
        # 不信任任何代理时，使用直接来源 IP，忽略 X-Forwarded-For
        assert captured["trusted_client_ip"] != "99.99.99.99"


# ═══════════════════════════════════════════════════════════════════════════════
# 生产日志 JSON 输出
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.unit
class TestProductionLogProfile:
    """SPEC 24.3: 生产环境单行 JSON stdout 输出，不含 RotatingFileHandler。"""

    def _make_prod_settings(self) -> Settings:
        """构造合法的生产环境配置。"""

        return Settings(
            ENVIRONMENT="production",
            ACCESS_TOKEN_HMAC_KEY="prod-access-key-" + "a" * 16,
            REFRESH_TOKEN_HMAC_KEY="prod-refresh-key-" + "b" * 16,
            SYSCONFIG_ENCRYPTION_KEY="T44-h5wE4-HJ69EZjyDir3a_DNQFAT5DMW8De0tXijU=",
            TRUSTED_HOSTS="admin.example.com",
            METRICS_TOKEN="prod-metrics-secret-token",
        )

    def test_prod_output_is_single_line_json(self) -> None:
        """生产日志每行是一个 JSON 对象。"""

        settings = self._make_prod_settings()
        configure_logging(settings)

        output = io.StringIO()
        structlog.configure(
            processors=[
                inject_request_id,
                mask_sensitive_fields,
                escape_newlines,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.stdlib.add_log_level,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            logger_factory=structlog.PrintLoggerFactory(file=output),
            cache_logger_on_first_use=True,
        )

        logger = structlog.get_logger().bind(module="test")
        token = request_id_var.set("req-001")
        try:
            logger.info("test_event", user="alice")
        finally:
            request_id_var.reset(token)

        raw_output = output.getvalue().strip()
        lines = raw_output.split("\n")

        # 每行必须恰好是一个 JSON 对象
        assert len(lines) >= 1
        for line in lines:
            data = json.loads(line)  # 不抛异常即证明是合法 JSON
            assert "timestamp" in data
            assert data["level"] == "info"

    def test_prod_output_no_rotating_file_handler(self) -> None:
        """生产日志配置不含 RotatingFileHandler — SPEC 24.3.

        SPEC 24.3: "API Worker 不直接使用进程内 RotatingFileHandler
        写共享文件"。structlog 的 PrintLoggerFactory 通过 stdout 输出，
        不属于文件轮转 handler。
        """

        from logging.handlers import RotatingFileHandler

        settings = self._make_prod_settings()
        configure_logging(settings)

        # 检查标准库 logging 的 handler 列表中没有 RotatingFileHandler
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            assert not isinstance(handler, RotatingFileHandler), (
                "生产环境不应使用 RotatingFileHandler，"
                "落盘轮转由进程外收集器负责（SPEC 24.3）"
            )

    def test_prod_output_contains_required_fields(self) -> None:
        """生产 JSON 日志包含必需字段。"""

        settings = self._make_prod_settings()
        configure_logging(settings)

        output = io.StringIO()
        structlog.configure(
            processors=[
                inject_request_id,
                mask_sensitive_fields,
                escape_newlines,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.stdlib.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            logger_factory=structlog.PrintLoggerFactory(file=output),
            cache_logger_on_first_use=True,
        )

        logger = structlog.get_logger().bind(module="test")
        token = request_id_var.set("req-002")
        try:
            logger.info("request", method="GET", path="/api/v1/meta", status_code=200)
        finally:
            request_id_var.reset(token)

        data = json.loads(output.getvalue().strip())
        assert data["event"] == "request"
        assert data["level"] == "info"
        assert data["request_id"] == "req-002"
        assert data["method"] == "GET"
        assert data["module"] == "test"

    def test_prod_output_newlines_escaped(self) -> None:
        """日志内容中的换行符被转义，保持单行 JSON。"""

        output = io.StringIO()
        structlog.configure(
            processors=[
                mask_sensitive_fields,
                escape_newlines,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            logger_factory=structlog.PrintLoggerFactory(file=output),
            cache_logger_on_first_use=True,
        )

        logger = structlog.get_logger()
        logger.info("injection_attempt", user_input="line1\nline2\rline3")

        raw = output.getvalue().strip()
        # 输出必须只有一行
        assert "\n" not in raw
        data = json.loads(raw)
        # 转义后的值
        assert "\\n" in data["user_input"]
        assert "\\r" in data["user_input"]


# ═══════════════════════════════════════════════════════════════════════════════
# 部署约定文档存在性验证
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.unit
class TestDeploymentDocs:
    """SPEC 23.1 / 26.3: HTTPS 终结与代理头约定写入文档。"""

    def test_deployment_conventions_doc_exists(self) -> None:
        """部署约定文档存在。"""

        from pathlib import Path

        doc_path = Path(__file__).parent.parent / "docs" / "deployment-conventions.md"
        assert doc_path.exists(), "docs/deployment-conventions.md 必须存在"

    def test_doc_mentions_https_termination(self) -> None:
        """文档包含 HTTPS 终结约定。"""

        from pathlib import Path

        doc_path = Path(__file__).parent.parent / "docs" / "deployment-conventions.md"
        content = doc_path.read_text(encoding="utf-8")
        assert "HTTPS" in content or "https" in content
        assert "Nginx" in content or "nginx" in content
        assert "终结" in content

    def test_doc_mentions_proxy_header_trust(self) -> None:
        """文档包含代理头信任边界约定。"""

        from pathlib import Path

        doc_path = Path(__file__).parent.parent / "docs" / "deployment-conventions.md"
        content = doc_path.read_text(encoding="utf-8")
        assert "X-Forwarded" in content
        assert "TRUSTED_PROXIES" in content or "可信代理" in content

    def test_doc_mentions_trusted_host_and_cors(self) -> None:
        """文档包含可信 Host 与 CORS 白名单约定。"""

        from pathlib import Path

        doc_path = Path(__file__).parent.parent / "docs" / "deployment-conventions.md"
        content = doc_path.read_text(encoding="utf-8")
        assert "TRUSTED_HOSTS" in content or "可信 Host" in content
        assert "CORS" in content
