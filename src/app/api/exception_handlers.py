"""API 边界异常处理器 — 异常到 RFC 9457 problem+json 响应的统一转换.

SPEC 9.3 / 10.1 / 23.3 / 24.1:
  - 错误响应固定采用 RFC 9457 ``application/problem+json``。
  - 在 API 边界统一完成异常到 HTTP 响应的转换。
  - 未处理异常记录完整日志，响应不泄露调用栈和内部路径。
  - 避免重复记录同一个异常。

处理器映射:
  1. ``ApplicationError`` 及子类
     → ``urn:apex:problem:<小写错误码>`` problem+json（SPEC 9.3）。
  2. ``RequestValidationError``（FastAPI/Pydantic 字段校验）
     → 422 problem+json，含 ``errors`` 数组（SPEC 9.3）。
  3. ``StarletteHTTPException``（框架级 HTTP 异常）
     → problem+json，``type`` 为 ``about:blank``。
  4. 未处理 ``Exception``
     → 500 problem+json，``type`` 为 ``about:blank``，
       服务端日志含完整堆栈（SPEC 24.1），响应不含堆栈（SPEC 23.3）。
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import structlog
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import ApplicationError, ValidationError

if TYPE_CHECKING:
    from fastapi import Request

# RFC 9457 Content-Type
_PROBLEM_CONTENT_TYPE = "application/problem+json"

# 业务错误 urn 前缀 — SPEC 9.3: "urn:apex:problem: 加小写错误码"
_URN_PREFIX = "urn:apex:problem:"

# Request ID 请求头名称（与 RequestContextMiddleware 一致）
_REQUEST_ID_HEADER = "X-Request-ID"


def _get_request_id(request: Request) -> str:
    """从请求 scope 获取 Request ID.

    Request ID 由 RequestContextMiddleware 存入 scope，
    确保即使在中间件链异常退出后（如未处理异常经 ServerErrorMiddleware
    处理），异常处理器仍能读取到正确的 Request ID（SPEC 9.5）。
    """

    rid = request.scope.get("request_id", "")
    return str(rid) if rid else ""


def _build_problem_body(
    *,
    status: int,
    type_: str,
    title: str,
    detail: str,
    instance: str,
    code: str,
    request_id: str,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """构建 RFC 9457 problem+json 响应体.

    SPEC 9.3: 固定包含 type/title/status/detail/instance/code/request_id。
    字段校验错误额外包含 ``errors`` 数组。
    """

    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code,
        "request_id": request_id,
    }
    if errors is not None:
        body["errors"] = errors
    return body


def _problem_response(
    *,
    status: int,
    type_: str,
    title: str,
    detail: str,
    instance: str,
    code: str,
    request_id: str,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    """构建 ``application/problem+json`` JSONResponse.

    所有 problem+json 响应回写 ``X-Request-ID`` 头（SPEC 9.5），
    确保客户端可关联请求与错误响应。
    """

    body = _build_problem_body(
        status=status,
        type_=type_,
        title=title,
        detail=detail,
        instance=instance,
        code=code,
        request_id=request_id,
        errors=errors,
    )
    response = JSONResponse(
        status_code=status,
        content=body,
        media_type=_PROBLEM_CONTENT_TYPE,
    )
    if request_id:
        response.headers[_REQUEST_ID_HEADER] = request_id
    return response


async def application_error_handler(
    request: Request,
    exc: ApplicationError,
) -> JSONResponse:
    """``ApplicationError`` 及子类 → problem+json 响应.

    SPEC 9.3: 业务错误的 ``type`` 为 ``urn:apex:problem:<小写错误码>``。
    从错误码注册表获取 HTTP 状态码和含义元数据。

    ``ValidationError`` 子类额外携带 ``errors`` 数组（SPEC 9.3）。
    业务错误不被包装为 HTTP 200（SPEC 9.3）。
    """

    request_id = _get_request_id(request)
    metadata = default_registry.get(exc.code)
    http_status = metadata.http_status if metadata else 500
    title = metadata.meaning if metadata else "应用错误"
    detail = str(exc) if str(exc) else title
    type_ = f"{_URN_PREFIX}{exc.code.lower()}"

    # ValidationError 携带字段级错误数组
    errors: list[dict[str, str]] | None = None
    if isinstance(exc, ValidationError) and exc.field_errors:
        errors = [
            {
                "field": fe.field,
                "reason": fe.reason,
                "message": fe.message,
            }
            for fe in exc.field_errors
        ]

    return _problem_response(
        status=http_status,
        type_=type_,
        title=title,
        detail=detail,
        instance=str(request.url.path),
        code=exc.code,
        request_id=request_id,
        errors=errors,
    )


def _convert_fastapi_validation_error(
    error: dict[str, Any],
) -> dict[str, str]:
    """将 FastAPI 校验错误转换为 field/reason/message 格式.

    SPEC 9.3: errors 数组元素固定包含 field、reason 和 message。

    FastAPI 错误结构::

        {"type": "missing", "loc": ["body", "field"], "msg": "Field required"}
    """

    loc = error.get("loc", [])
    field = ".".join(str(part) for part in loc) if loc else ""
    reason = str(error.get("type", "invalid"))
    message = str(error.get("msg", ""))
    return {"field": field, "reason": reason, "message": message}


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """FastAPI ``RequestValidationError`` → 422 problem+json.

    SPEC 9.3: 字段校验错误返回 422 且含 ``errors`` 数组。
    框架级验证错误使用 ``about:blank`` 作为 ``type``
    （SPEC 9.3: "无业务错误码的框架级错误使用 about:blank"）。
    """

    request_id = _get_request_id(request)
    errors = [_convert_fastapi_validation_error(e) for e in exc.errors()]

    return _problem_response(
        status=422,
        type_="about:blank",
        title="字段校验失败",
        detail="请求参数校验未通过",
        instance=str(request.url.path),
        code="VALIDATION.FAILED",
        request_id=request_id,
        errors=errors,
    )


def _http_status_to_code(status: int) -> str:
    """从 HTTP 状态码派生框架级错误码.

    例如 404 → ``HTTP.NOT_FOUND``，405 → ``HTTP.METHOD_NOT_ALLOWED``。
    无法识别的状态码使用 ``HTTP.ERROR`` 兜底。
    """

    try:
        phrase = HTTPStatus(status).phrase
        return f"HTTP.{phrase.upper().replace(' ', '_')}"
    except ValueError:
        return "HTTP.ERROR"


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Starlette/FastAPI ``HTTPException`` → problem+json.

    SPEC 9.3: 所有错误响应采用 problem+json。
    框架级 HTTP 异常使用 ``about:blank`` 作为 ``type``。
    """

    request_id = _get_request_id(request)
    status = exc.status_code
    try:
        title = HTTPStatus(status).phrase
    except ValueError:
        title = "HTTP 错误"

    return _problem_response(
        status=status,
        type_="about:blank",
        title=title,
        detail=str(exc.detail) if exc.detail else title,
        instance=str(request.url.path),
        code=_http_status_to_code(status),
        request_id=request_id,
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """未处理异常 → 500 problem+json.

    SPEC 10.1: "未处理异常记录完整日志"。
    SPEC 23.3: "响应中不泄露内部路径、SQL、配置和调用栈"。
    SPEC 24.1: "异常日志包含完整内部堆栈"、"避免重复记录同一个异常"。

    此处理器是异常处理的最后一道防线，确保所有未捕获异常
    返回统一的 problem+json 响应，不向客户端泄露内部信息。
    完整堆栈仅在服务端日志中记录一次（此处理器是唯一记录点）。
    """

    request_id = _get_request_id(request)

    # 记录完整堆栈（SPEC 24.1），使用 exc_info 确保堆栈格式化。
    # 此处是异常的唯一日志记录点，中间件只记录请求级信息不记录异常，
    # 因此不存在重复记录（SPEC 24.1: "避免重复记录同一个异常"）。
    # 每次获取新 logger 实例，拾取当前 structlog 配置（如测试中的 capture_logs）。
    logger = structlog.get_logger().bind(module="app.api.exception_handlers")
    logger.error(
        "未处理异常",
        error_type=type(exc).__name__,
        error_message=str(exc),
        request_id=request_id,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

    return _problem_response(
        status=500,
        type_="about:blank",
        title="系统内部错误",
        detail="服务器处理请求时发生内部错误",
        instance=str(request.url.path),
        code="SYSTEM.INTERNAL",
        request_id=request_id,
    )


def register_exception_handlers(app: Any) -> None:
    """向 FastAPI 应用注册全部异常处理器.

    SPEC 10.1: "在 API 边界统一完成异常到 HTTP 响应的转换"。

    Starlette/FastAPI 使用异常类型的 MRO 查找最具体的处理器，
    因此注册顺序不影响匹配优先级:
      - ``ApplicationError`` 及子类由 ``application_error_handler`` 处理。
      - ``RequestValidationError`` 由 ``validation_error_handler`` 处理。
      - ``StarletteHTTPException`` 由 ``http_exception_handler`` 处理。
      - 其余未处理异常由 ``unhandled_exception_handler`` 兜底。
    """

    app.add_exception_handler(ApplicationError, application_error_handler)
    app.add_exception_handler(
        RequestValidationError,
        validation_error_handler,
    )
    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
