"""请求指标采集中间件 — SPEC 24.2.

为每个 HTTP 请求更新 Prometheus 指标:
  - 请求计数（``apex_http_requests_total``）
  - 错误计数（``apex_http_request_errors_total``, 状态码 ≥ 400）
  - 请求耗时直方图（``apex_http_request_duration_seconds``）

当请求耗时超过 ``SLOW_REQUEST_THRESHOLD_MS`` 时，记录结构化慢请求日志
（SPEC 24.2: "可以识别慢接口"）。

端点标签使用路由模板路径（如 ``/api/v1/users/{user_id}``），
而非实际路径（如 ``/api/v1/users/123``），避免高基数标签问题。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.metrics.registry import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUEST_ERRORS_TOTAL,
    HTTP_REQUESTS_TOTAL,
)

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

_logger = structlog.get_logger().bind(module="app.core.metrics.middleware")


def _get_endpoint(request: Request) -> str:
    """提取请求的路由模板路径.

    优先使用 Starlette 路由匹配后写入 scope 的路由模板路径，
    避免路径参数导致标签高基数。无匹配路由时回退到实际请求路径。
    """

    route: Any = request.scope.get("route")
    if route is not None:
        path: Any = getattr(route, "path", None)
        if path is not None:
            return str(path)
    return request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    """请求指标采集中间件 — SPEC 24.2.

    在请求完成后更新计数器与直方图，并检测慢请求。

    参数:
        app: ASGI 应用（由 Starlette 传入）。
        slow_request_threshold_ms: 慢请求阈值（毫秒），
            超过时记录结构化 ``slow_request`` 警告日志。
    """

    def __init__(
        self,
        app: Any,
        *,
        slow_request_threshold_ms: int = 2000,
    ) -> None:
        """初始化请求指标采集中间件."""

        super().__init__(app)
        self._slow_threshold_ms = slow_request_threshold_ms

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """处理请求并更新指标."""

        start = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_s = time.perf_counter() - start
            duration_ms = round(duration_s * 1000, 2)

            endpoint = _get_endpoint(request)
            method = request.method
            status_str = str(status_code)

            # 更新请求计数（SPEC 24.2: 请求数量）
            HTTP_REQUESTS_TOTAL.labels(
                method=method,
                endpoint=endpoint,
                status=status_str,
            ).inc()

            # 更新错误计数（SPEC 24.2: 错误数量，状态码 ≥ 400）
            if status_code >= 400:
                HTTP_REQUEST_ERRORS_TOTAL.labels(
                    method=method,
                    endpoint=endpoint,
                ).inc()

            # 更新请求耗时直方图（SPEC 24.2: 请求耗时）
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration_s)

            # 慢请求识别（SPEC 24.2: 可以识别慢接口）
            if duration_ms > self._slow_threshold_ms:
                _logger.warning(
                    "slow_request",
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    threshold_ms=self._slow_threshold_ms,
                )
