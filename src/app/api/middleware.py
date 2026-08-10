"""请求中间件 — Request ID 生成与请求日志.

SPEC 9.5 / 24.1:
  - 每个请求生成或接收唯一 Request ID。
  - Request ID 写入响应头和结构化日志。
  - 请求日志包含方法、路径、状态码和耗时。

SPEC 5.8:
  - ContextVar（``request_id_var``）仅用于日志关联，
    不作为业务授权、事务或领域状态的数据源。

中间件职责:
  1. 从入站 ``X-Request-ID`` 头读取 Request ID；缺失时生成。
  2. 设置到 ``request_id_var``（ContextVar），供日志处理器读取。
  3. 记录请求日志（方法、路径、状态码、耗时）。
  4. 将 Request ID 写入响应头 ``X-Request-ID``。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.request_context import request_id_var

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

# 入站 Request ID 头名称
_REQUEST_ID_HEADER = "X-Request-ID"

# 请求日志记录器，绑定模块标识（SPEC 24.1: 日志包含模块）
_request_logger = structlog.get_logger().bind(module="app.api.middleware")


def _generate_request_id() -> str:
    """生成新的 Request ID（UUID v4 十六进制形式）."""

    return uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """请求上下文中间件 — Request ID 管理与请求日志.

    合并 Request ID 注入与请求日志记录两个职责到单一中间件，
    避免多层中间件重复处理同一请求。

    流程:
      1. 从入站头获取或生成 Request ID。
      2. 设置到 ContextVar（仅供日志关联，SPEC 5.8）。
      3. 记录开始时间，处理请求。
      4. 记录请求日志（方法/路径/状态码/耗时）。
      5. 将 Request ID 写入响应头。
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """处理单个请求: 注入 Request ID、记录日志、回写响应头."""

        # 从入站头获取 Request ID，缺失时生成（SPEC 9.5）
        request_id = request.headers.get(_REQUEST_ID_HEADER)
        if not request_id:
            request_id = _generate_request_id()

        # 设置 ContextVar — 仅用于日志关联（SPEC 5.8）
        token = request_id_var.set(request_id)

        # 将 Request ID 存入 scope，供异常处理器在中间件无法回写
        # 响应头时（如未处理异常经 ServerErrorMiddleware 处理）读取。
        # request_id 是请求关联标识，非业务状态（SPEC 5.8）。
        request.scope["request_id"] = request_id

        # 记录开始时间用于耗时计算（SPEC 24.1: 请求日志包含耗时）
        start = time.perf_counter()
        status_code = 500
        response: Response | None = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            # 记录请求日志（SPEC 24.1: 方法/路径/状态码/耗时）
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            _request_logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
            )

            # 将 Request ID 写入响应头（SPEC 9.5）
            if response is not None:
                response.headers[_REQUEST_ID_HEADER] = request_id

            # 重置 ContextVar 到先前的值
            request_id_var.reset(token)
