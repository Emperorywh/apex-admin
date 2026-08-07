"""API 边界异常处理器（SPEC §10.1、§9.3）。

在 API 边界统一完成异常到 RFC 9457 ``application/problem+json`` 响应的转换。
SPEC §10.1：在 API 边界统一完成异常到 HTTP 响应的转换。

处理器覆盖四类异常来源：

1. ``AppError`` 及其子类 — 应用层主动抛出的异常（含业务错误码）
2. ``RequestValidationError`` — Pydantic 请求体校验失败（字段级 errors 数组）
3. ``StarletteHTTPException`` — 框架级 HTTP 异常（404 路由不存在、405 方法不允许等）
4. ``Exception`` — 未处理异常（记录完整堆栈，响应不暴露内部细节）

Starlette 按异常 MRO 从具体到通用匹配处理器，最具体的处理器优先。
因此 ``AppError`` 的处理器优先于 ``Exception`` 的兜底处理器。

所有响应 Content-Type 固定为 ``application/problem+json``（SPEC §9.3）。
响应不得将业务错误包装为 HTTP 200（SPEC §9.3）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.problem import ProblemDetail
from app.errors import build_problem_type
from app.errors.base import AppError, FieldError
from app.middleware.request_id import get_request_id

_logger = logging.getLogger("app.api.handlers")

# Content-Type 固定为 application/problem+json（SPEC §9.3）
_PROBLEM_CONTENT_TYPE = "application/problem+json"

# HTTP 状态码 → 简体中文标题
_HTTP_STATUS_TITLES: dict[int, str] = {
    400: "参数错误",
    401: "未认证",
    403: "禁止访问",
    404: "资源不存在",
    405: "方法不允许",
    409: "状态冲突",
    413: "请求体过大",
    415: "不支持的媒体类型",
    422: "参数校验失败",
    500: "系统错误",
}

# HTTP 状态码 → 框架级稳定错误码
_HTTP_STATUS_CODES: dict[int, str] = {
    400: "APP.PARAMETER",
    401: "APP.UNAUTHENTICATED",
    403: "APP.FORBIDDEN",
    404: "APP.NOT_FOUND",
    405: "APP.METHOD_NOT_ALLOWED",
    409: "APP.CONFLICT",
    413: "APP.PAYLOAD_TOO_LARGE",
    415: "APP.UNSUPPORTED_MEDIA_TYPE",
    422: "APP.PARAMETER",
    500: "APP.SYSTEM_ERROR",
}


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 应用上注册全部 RFC 9457 异常处理器。

    注册后，所有未捕获的异常都会被转换为 ``application/problem+json`` 响应，
    不再出现 FastAPI 默认的错误格式或裸 500 堆栈。

    Args:
        app: 待注册处理器的 FastAPI 应用实例
    """
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unhandled)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _resolve_request_id(request: Request) -> str:
    """获取请求 ID：优先从 ContextVar（中间件内），回退到 ASGI scope state。

    ServerErrorMiddleware 位于 RequestIdMiddleware 之外，当未处理异常
    传播到 ServerErrorMiddleware 时 ContextVar 已被重置，
    此时从 scope state 中获取由中间件写入的 request_id。
    """
    rid = get_request_id()
    if rid:
        return rid
    state = request.scope.get("state", {})
    return str(state.get("request_id", ""))


def _build_response(problem: ProblemDetail) -> JSONResponse:
    """将 ProblemDetail 转换为 application/problem+json JSONResponse。"""
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(exclude_none=True, mode="json"),
        media_type=_PROBLEM_CONTENT_TYPE,
    )


def _convert_pydantic_errors(
    validation_errors: Sequence[Any],
) -> list[FieldError]:
    """将 Pydantic 校验错误列表转换为 FieldError 列表（SPEC §9.3）。

    Pydantic 错误的 ``loc`` 元组首元素通常为 ``"body"``、``"query"``、
    ``"path"`` 或 ``"header"``，表示参数来源；后续元素为字段路径。
    """
    field_errors: list[FieldError] = []
    for err in validation_errors:
        loc: Any = err.get("loc", ())
        # loc 首元素为参数来源（body/query/path/header），跳过取后续路径
        if isinstance(loc, (tuple, list)) and len(loc) > 1:
            field_path = ".".join(str(part) for part in loc[1:])
        elif isinstance(loc, (tuple, list)) and len(loc) == 1:
            field_path = str(loc[0])
        else:
            field_path = ""
        field_errors.append(
            FieldError(
                field=field_path,
                reason=str(err.get("type", "invalid")),
                message=str(err.get("msg", "校验失败")),
            )
        )
    return field_errors


# ---------------------------------------------------------------------------
# 异常处理器
# ---------------------------------------------------------------------------


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """处理 ``AppError`` 及其子类 → RFC 9457 ProblemDetail。

    业务错误码（非 ``APP`` / ``DB`` 前缀）使用
    ``urn:apex:problem:<小写错误码>`` 作为 type；
    框架级错误码使用 ``about:blank``（SPEC §9.3）。
    """
    assert isinstance(exc, AppError)
    problem = ProblemDetail(
        type=build_problem_type(exc.code),
        title=exc.title,
        status=exc.http_status,
        detail=exc.detail,
        instance=str(request.url.path),
        code=exc.code,
        request_id=_resolve_request_id(request),
        errors=exc.errors,
    )
    return _build_response(problem)


async def _handle_validation_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """处理 Pydantic 请求体校验失败 → RFC 9457 ProblemDetail（含 errors 数组）。

    请求体校验失败是框架级错误，type 使用 ``about:blank``，
    code 使用 ``APP.PARAMETER``，HTTP 状态码 422（SPEC §9.3）。
    errors 数组元素固定含 field、reason、message。
    """
    assert isinstance(exc, RequestValidationError)
    field_errors = _convert_pydantic_errors(exc.errors())
    problem = ProblemDetail(
        type="about:blank",
        title="参数校验失败",
        status=422,
        detail="请求参数校验失败",
        instance=str(request.url.path),
        code="APP.PARAMETER",
        request_id=_resolve_request_id(request),
        errors=field_errors,
    )
    return _build_response(problem)


async def _handle_http_exception(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """处理 Starlette HTTP 异常 → RFC 9457 ProblemDetail。

    覆盖 404 路由不存在、405 方法不允许等框架级 HTTP 异常。
    这些异常没有业务错误码，type 使用 ``about:blank``（SPEC §9.3）。
    """
    assert isinstance(exc, StarletteHTTPException)
    status_code = exc.status_code
    title = _HTTP_STATUS_TITLES.get(status_code, "HTTP 错误")
    code = _HTTP_STATUS_CODES.get(status_code, "APP.HTTP_ERROR")
    detail = str(exc.detail) if exc.detail else title

    problem = ProblemDetail(
        type="about:blank",
        title=title,
        status=status_code,
        detail=detail,
        instance=str(request.url.path),
        code=code,
        request_id=_resolve_request_id(request),
    )
    return _build_response(problem)


async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
    """处理未处理异常 → RFC 9457 500 响应（SPEC §10.1、§23.3）。

    记录完整异常堆栈到结构化日志（SPEC §24.1）。
    响应不返回调用栈和内部实现细节（SPEC §10.1、§23.3），
    只返回通用的系统错误信息和稳定错误码 ``APP.SYSTEM_ERROR``。
    """
    _logger.error("未处理异常", exc_info=True)

    problem = ProblemDetail(
        type="about:blank",
        title="系统错误",
        status=500,
        detail="服务器内部错误",
        instance=str(request.url.path),
        code="APP.SYSTEM_ERROR",
        request_id=_resolve_request_id(request),
    )
    return _build_response(problem)
