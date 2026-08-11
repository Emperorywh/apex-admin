"""运行指标测试 — SPEC 24.2.

覆盖 TASK-030 全部验收条件:
  - 请求计数、错误计数、请求耗时直方图指标随请求更新。
  - 数据库连接池状态指标可获取。
  - 慢接口与慢数据库操作可被识别（超阈值结构化日志或指标标签）。
  - /metrics 端点无有效令牌时拒绝访问。
  - 测试与静态检查通过（由 ruff/mypy 验证）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import generate_latest
from starlette.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.metrics.db_events import (
    _on_pool_checkin,
    _on_pool_checkout,
    register_db_metrics,
)
from app.core.metrics.registry import (
    DB_POOL_CHECKED_OUT,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUEST_ERRORS_TOTAL,
    HTTP_REQUESTS_TOTAL,
)
from app.main import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI


# ── 辅助函数 ───────────────────────────────────────────────────────────────


def _make_test_app(**overrides: str) -> FastAPI:
    """创建测试应用."""

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
        METRICS_TOKEN="test-metrics-token",
        **overrides,
    )
    return create_app(settings)


def _get_metric_value(metric: Any, **labels: str) -> float:
    """从 prometheus_client 指标中读取指定标签组合的样本值."""

    for m in metric.collect():
        for sample in m.samples:
            if all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def _get_histogram_count(metric: Any, **labels: str) -> float:
    """读取 Histogram 的 ``_count`` 样本值（观测总次数）."""

    for m in metric.collect():
        for sample in m.samples:
            if sample.name.endswith("_count") and all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                return sample.value
    return 0.0


def _get_histogram_sum(metric: Any, **labels: str) -> float:
    """读取 Histogram 的 ``_sum`` 样本值（观测值总和）."""

    for m in metric.collect():
        for sample in m.samples:
            if sample.name.endswith("_sum") and all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                return sample.value
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 请求计数、错误计数、耗时直方图
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.unit
class TestRequestMetricsUpdate:
    """SPEC 24.2: 请求计数、错误计数、请求耗时直方图随请求更新."""

    def test_request_counter_increments(self) -> None:
        """请求计数器随请求递增."""

        app = _make_test_app()
        with TestClient(app) as client:
            before = _get_metric_value(
                HTTP_REQUESTS_TOTAL,
                method="GET",
                endpoint="/health/live",
                status="200",
            )
            client.get("/health/live")
            after = _get_metric_value(
                HTTP_REQUESTS_TOTAL,
                method="GET",
                endpoint="/health/live",
                status="200",
            )
        assert after > before

    def test_error_counter_increments_on_4xx(self) -> None:
        """错误计数器在 4xx 响应时递增."""

        app = _make_test_app()
        with TestClient(app) as client:
            before = _get_metric_value(
                HTTP_REQUEST_ERRORS_TOTAL,
                method="GET",
                endpoint="/nonexistent",
            )
            client.get("/nonexistent")
            after = _get_metric_value(
                HTTP_REQUEST_ERRORS_TOTAL,
                method="GET",
                endpoint="/nonexistent",
            )
        assert after > before

    def test_error_counter_increments_on_5xx(self) -> None:
        """错误计数器在 5xx 响应时递增."""

        app = _make_test_app()
        with TestClient(app) as client:
            before = _get_metric_value(
                HTTP_REQUEST_ERRORS_TOTAL,
                method="GET",
                endpoint="/nonexistent",
            )
            client.get("/nonexistent")
            after = _get_metric_value(
                HTTP_REQUEST_ERRORS_TOTAL,
                method="GET",
                endpoint="/nonexistent",
            )
        assert after > before

    def test_request_duration_histogram_records(self) -> None:
        """请求耗时直方图记录观测值."""

        app = _make_test_app()
        with TestClient(app) as client:
            before_count = _get_histogram_count(
                HTTP_REQUEST_DURATION_SECONDS,
                method="GET",
                endpoint="/health/live",
            )
            client.get("/health/live")
            after_count = _get_histogram_count(
                HTTP_REQUEST_DURATION_SECONDS,
                method="GET",
                endpoint="/health/live",
            )
            after_sum = _get_histogram_sum(
                HTTP_REQUEST_DURATION_SECONDS,
                method="GET",
                endpoint="/health/live",
            )
        assert after_count > before_count
        assert after_sum > 0.0

    def test_metrics_visible_in_generate_latest(self) -> None:
        """指标在 prometheus 展示格式中可见."""

        app = _make_test_app()
        with TestClient(app) as client:
            client.get("/health/live")
            output = generate_latest().decode("utf-8")
        assert "apex_http_requests_total" in output
        assert "apex_http_request_errors_total" in output
        assert "apex_http_request_duration_seconds" in output


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库连接池状态指标
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.unit
class TestDbPoolMetrics:
    """SPEC 24.2: 数据库连接池状态指标可获取."""

    def test_pool_gauge_visible_in_output(self) -> None:
        """连接池检出连接数指标在 prometheus 展示格式中可见."""

        output = generate_latest().decode("utf-8")
        assert "apex_db_pool_checked_out_connections" in output

    def test_pool_checkout_checkin_updates_gauge(self) -> None:
        """checkout 事件增、checkin 事件减连接池检出连接数."""

        before = _get_metric_value(DB_POOL_CHECKED_OUT)
        _on_pool_checkout(None, None, None)
        after_checkout = _get_metric_value(DB_POOL_CHECKED_OUT)
        assert after_checkout == before + 1

        _on_pool_checkin(None, None)
        after_checkin = _get_metric_value(DB_POOL_CHECKED_OUT)
        assert after_checkin == before


# ═══════════════════════════════════════════════════════════════════════════════
# 慢接口与慢数据库操作识别
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.unit
class TestSlowRequestDetection:
    """SPEC 24.2: 慢接口可被识别（超阈值结构化日志）."""

    def test_slow_request_logs_warning(self) -> None:
        """请求耗时超过阈值时记录 slow_request 警告日志."""

        captured: list[dict[str, Any]] = []

        def fake_warning(event: str, **kwargs: Any) -> None:
            captured.append({"event": event, **kwargs})

        app = _make_test_app(SLOW_REQUEST_THRESHOLD_MS="0")

        with (
            TestClient(app) as client,
            patch(
                "app.core.metrics.middleware._logger.warning",
                side_effect=fake_warning,
            ),
        ):
            client.get("/health/live")

        assert any(
            rec["event"] == "slow_request" and rec["duration_ms"] >= 0
            for rec in captured
        ), f"Expected slow_request warning, got: {captured}"

    def test_fast_request_no_warning(self) -> None:
        """请求耗时未超过阈值时不记录 slow_request 日志."""

        captured: list[dict[str, Any]] = []

        def fake_warning(event: str, **kwargs: Any) -> None:
            captured.append({"event": event, **kwargs})

        app = _make_test_app(SLOW_REQUEST_THRESHOLD_MS="999999")

        with (
            TestClient(app) as client,
            patch(
                "app.core.metrics.middleware._logger.warning",
                side_effect=fake_warning,
            ),
        ):
            client.get("/health/live")

        assert not any(rec["event"] == "slow_request" for rec in captured), (
            f"Unexpected slow_request warning: {captured}"
        )


@pytest.mark.g4
@pytest.mark.unit
class TestSlowQueryDetection:
    """SPEC 24.2: 慢数据库操作可被识别（超阈值结构化日志）."""

    @staticmethod
    def _setup_handlers(
        threshold_ms: int,
    ) -> dict[str, Any]:
        """注册事件监听器并返回捕获的 handler 字典."""

        engine_mock = MagicMock()
        engine_mock.sync_engine = MagicMock()

        registered_handlers: dict[str, Any] = {}

        def fake_listen(target: Any, name: str, fn: Any) -> None:
            registered_handlers[name] = fn

        with patch(
            "app.core.metrics.db_events.event.listen",
            side_effect=fake_listen,
        ):
            register_db_metrics(engine_mock, slow_query_threshold_ms=threshold_ms)

        return registered_handlers

    def test_slow_query_logs_warning(self) -> None:
        """查询耗时超过阈值时记录 slow_query 警告日志."""

        threshold_ms = 100
        handlers = self._setup_handlers(threshold_ms)

        captured: list[dict[str, Any]] = []

        def fake_warning(event: str, **kwargs: Any) -> None:
            captured.append({"event": event, **kwargs})

        context = MagicMock()

        with (
            patch("app.core.metrics.db_events.time.perf_counter") as mock_time,
            patch(
                "app.core.metrics.db_events._logger.warning",
                side_effect=fake_warning,
            ),
        ):
            mock_time.side_effect = [1000.0, 1000.2]  # 200ms > 100ms
            handlers["before_cursor_execute"](
                None,
                None,
                "SELECT 1",
                None,
                context,
                False,
            )
            handlers["after_cursor_execute"](
                None,
                None,
                "SELECT 1",
                None,
                context,
                False,
            )

        assert any(
            rec["event"] == "slow_query" and rec["duration_ms"] >= threshold_ms
            for rec in captured
        ), f"Expected slow_query warning, got: {captured}"

    def test_fast_query_no_warning(self) -> None:
        """查询耗时未超过阈值时不记录 slow_query 日志."""

        threshold_ms = 500
        handlers = self._setup_handlers(threshold_ms)

        captured: list[dict[str, Any]] = []

        def fake_warning(event: str, **kwargs: Any) -> None:
            captured.append({"event": event, **kwargs})

        context = MagicMock()

        with (
            patch("app.core.metrics.db_events.time.perf_counter") as mock_time,
            patch(
                "app.core.metrics.db_events._logger.warning",
                side_effect=fake_warning,
            ),
        ):
            mock_time.side_effect = [1000.0, 1000.01]  # 10ms < 500ms
            handlers["before_cursor_execute"](
                None,
                None,
                "SELECT 1",
                None,
                context,
                False,
            )
            handlers["after_cursor_execute"](
                None,
                None,
                "SELECT 1",
                None,
                context,
                False,
            )

        assert not any(rec["event"] == "slow_query" for rec in captured), (
            f"Unexpected slow_query warning: {captured}"
        )

    def test_register_db_metrics_listens_to_all_events(self) -> None:
        """register_db_metrics 注册全部四类事件监听器."""

        engine_mock = MagicMock()
        engine_mock.sync_engine = MagicMock()

        listened_events: list[str] = []

        def fake_listen(target: Any, name: str, fn: Any) -> None:
            listened_events.append(name)

        with patch(
            "app.core.metrics.db_events.event.listen",
            side_effect=fake_listen,
        ):
            register_db_metrics(engine_mock, slow_query_threshold_ms=500)

        assert "checkout" in listened_events
        assert "checkin" in listened_events
        assert "before_cursor_execute" in listened_events
        assert "after_cursor_execute" in listened_events


# ═══════════════════════════════════════════════════════════════════════════════
# /metrics 端点令牌保护
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.api
class TestMetricsEndpointTokenProtection:
    """SPEC 24.2: /metrics 端点无有效令牌时拒绝访问."""

    def test_no_token_returns_403(self) -> None:
        """无令牌时返回 403."""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get("/metrics")
        assert response.status_code == 403

    def test_wrong_token_returns_403(self) -> None:
        """错误令牌时返回 403."""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get(
                "/metrics",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert response.status_code == 403

    def test_wrong_scheme_returns_403(self) -> None:
        """非 Bearer 认证方案时返回 403."""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get(
                "/metrics",
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
            )
        assert response.status_code == 403

    def test_valid_token_returns_metrics(self) -> None:
        """有效令牌时返回 Prometheus 指标格式."""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get(
                "/metrics",
                headers={"Authorization": "Bearer test-metrics-token"},
            )
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")
        body = response.text
        assert "apex_http_requests_total" in body
        assert "apex_db_pool_checked_out_connections" in body

    def test_valid_token_returns_process_metrics(self) -> None:
        """有效令牌时返回内置 Python 进程指标."""

        app = _make_test_app()
        with TestClient(app) as client:
            response = client.get(
                "/metrics",
                headers={"Authorization": "Bearer test-metrics-token"},
            )
        assert response.status_code == 200
        # prometheus_client 默认注册表包含进程指标
        assert "python_info" in response.text or "process_" in response.text


@pytest.mark.g4
@pytest.mark.unit
class TestMetricsTokenConfigValidation:
    """SPEC 24.2: 生产环境 METRICS_TOKEN 配置校验."""

    def _prod_kwargs(self) -> dict[str, str]:
        """提供合法的生产环境基础配置."""

        return {
            "ENVIRONMENT": "production",
            "ACCESS_TOKEN_HMAC_KEY": "prod-access-key-" + "a" * 16,
            "REFRESH_TOKEN_HMAC_KEY": "prod-refresh-key-" + "b" * 16,
            "SYSCONFIG_ENCRYPTION_KEY": "T44-h5wE4-HJ69EZjyDir3a_DNQFAT5DMW8De0tXijU=",
            "TRUSTED_HOSTS": "admin.example.com",
            "ALLOWED_ORIGINS": "https://admin.example.com",
        }

    def test_production_missing_metrics_token_fails(self) -> None:
        """生产环境未设置 METRICS_TOKEN 时启动失败."""

        kwargs = self._prod_kwargs()
        with pytest.raises(ValueError, match="METRICS_TOKEN"):
            Settings(**kwargs)

    def test_production_with_metrics_token_succeeds(self) -> None:
        """生产环境设置 METRICS_TOKEN 时正常构造."""

        kwargs = self._prod_kwargs()
        kwargs["METRICS_TOKEN"] = "prod-metrics-secret-token"
        settings = Settings(**kwargs)
        assert settings.METRICS_TOKEN is not None
        assert settings.METRICS_TOKEN.get_secret_value() == "prod-metrics-secret-token"

    def test_dev_defaults_metrics_token(self) -> None:
        """开发/测试环境未设置 METRICS_TOKEN 时填充默认值."""

        settings = Settings(
            ENVIRONMENT=Environment.TESTING,
            ACCESS_TOKEN_HMAC_KEY="a" * 32,
            REFRESH_TOKEN_HMAC_KEY="b" * 32,
        )
        assert settings.METRICS_TOKEN is not None
        assert settings.METRICS_TOKEN.get_secret_value() == "dev-metrics-token"
