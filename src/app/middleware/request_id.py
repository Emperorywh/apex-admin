"""Request ID 中间件（SPEC §9.5）。

每请求生成或接收唯一 Request ID，写入响应头 ``X-Request-ID`` 并通过 ContextVar
关联结构化日志。

ContextVar 只允许用于日志关联，不得作为业务授权、事务或领域状态的数据源（SPEC §5.8）。
业务上下文通过 :class:`~app.context.UseCaseContext` 显式传递。
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Request ID 请求头名称
REQUEST_ID_HEADER = "X-Request-ID"

# Request ID ContextVar：仅用于日志关联（SPEC §5.8）
# 公开以供日志格式化器和测试使用，但严禁用于业务授权、事务或领域状态
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# 请求日志 logger
_request_logger = logging.getLogger("app.request")


def get_request_id() -> str | None:
    """获取当前请求的 Request ID。

    仅供日志格式化器和日志关联使用，不得用于业务逻辑或授权判断（SPEC §5.8）。
    """
    return request_id_var.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Request ID 中间件（SPEC §9.5）。

    处理流程：
    1. 从请求头 ``X-Request-ID`` 获取 Request ID，缺失时生成新的 UUID4。
    2. 将 Request ID 存入 ContextVar，供日志格式化器关联。
    3. 请求完成后记录访问日志（方法、路径、状态码、耗时）。
    4. 将 Request ID 写入响应头 ``X-Request-ID``。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # 从请求头获取或生成 Request ID
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())

        # 设置 ContextVar 供日志关联
        token = request_id_var.set(request_id)
        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        # 计算耗时并记录请求日志（SPEC §24.1：请求日志包含方法、路径、状态码和耗时）
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        _request_logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        # 写入响应头
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
