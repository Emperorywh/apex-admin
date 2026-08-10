"""异常体系、错误码与 RFC 9457 错误响应测试 — SPEC 9.3 / 10.1 / 10.2 / 23.3 / 24.1.

覆盖:
  - 异常层级与错误码属性（SPEC 10.1）。
  - 错误码注册表格式校验、重复拒绝和元数据（SPEC 10.2）。
  - problem+json 响应固定字段与 Content-Type（SPEC 9.3）。
  - urn type 规则与 request_id 一致性（SPEC 9.3）。
  - 字段校验错误 422 与 errors 数组（SPEC 9.3）。
  - 未处理异常 500 不含堆栈，服务端日志含完整堆栈（SPEC 23.3 / 24.1）。
  - 业务错误不被包装为 HTTP 200（SPEC 9.3）。

测试通过临时路由驱动 ASGI 应用验证响应结构。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.core.errors.codes import (
    DuplicateErrorCodeError,
    ErrorCodeRegistry,
    InvalidErrorCodeFormatError,
    default_registry,
)
from app.core.errors.exceptions import (
    ApplicationError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DatabaseConnectionError,
    FieldError,
    NotFoundError,
    ParameterError,
    SystemError,
    UniqueViolationError,
    ValidationError,
)

# ── 异常层级与错误码属性 ─────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_exception_hierarchy_inheritance() -> None:
    """异常层级继承关系正确（SPEC 10.1）."""

    assert issubclass(ParameterError, ApplicationError)
    assert issubclass(ValidationError, ParameterError)
    assert issubclass(AuthenticationError, ApplicationError)
    assert issubclass(AuthorizationError, ApplicationError)
    assert issubclass(NotFoundError, ApplicationError)
    assert issubclass(ConflictError, ApplicationError)
    assert issubclass(SystemError, ApplicationError)
    # UniqueViolationError 继承 ConflictError（唯一约束冲突即状态冲突）
    assert issubclass(UniqueViolationError, ConflictError)
    # DatabaseConnectionError 继承 SystemError（连接故障属系统级错误）
    assert issubclass(DatabaseConnectionError, SystemError)


@pytest.mark.g1
@pytest.mark.unit
def test_exception_codes_are_stable_strings() -> None:
    """每个异常类具有稳定的错误码（SPEC 10.2）."""

    assert ApplicationError.code == "APPLICATION.ERROR"
    assert ParameterError.code == "PARAMETER.INVALID"
    assert ValidationError.code == "VALIDATION.FAILED"
    assert AuthenticationError.code == "AUTH.UNAUTHENTICATED"
    assert AuthorizationError.code == "AUTH.FORBIDDEN"
    assert NotFoundError.code == "COMMON.NOT_FOUND"
    assert ConflictError.code == "COMMON.CONFLICT"
    assert SystemError.code == "SYSTEM.INTERNAL"
    assert UniqueViolationError.code == "DB.UNIQUE_VIOLATION"
    assert DatabaseConnectionError.code == "DB.CONNECTION_ERROR"


@pytest.mark.g1
@pytest.mark.unit
def test_validation_error_carries_field_errors() -> None:
    """ValidationError 携带 field_errors 数组（SPEC 9.3）."""

    errors = [
        FieldError(field="email", reason="missing", message="字段必填"),
        FieldError(field="age", reason="value_error", message="必须为正整数"),
    ]
    exc = ValidationError("校验失败", errors=errors)

    assert exc.field_errors == errors
    assert len(exc.field_errors) == 2
    assert exc.field_errors[0].field == "email"


@pytest.mark.g1
@pytest.mark.unit
def test_field_error_is_immutable() -> None:
    """FieldError 为不可变 dataclass."""

    fe = FieldError(field="x", reason="missing", message="必填")
    with pytest.raises(AttributeError):
        fe.field = "y"  # type: ignore[misc]


@pytest.mark.g1
@pytest.mark.unit
def test_exceptions_do_not_import_fastapi() -> None:
    """异常层级不依赖 FastAPI 或 HTTP 模块（SPEC 10.1）."""

    import inspect

    from app.core.errors import exceptions as exc_module

    source = inspect.getsource(exc_module)
    # 确认源码不含 FastAPI 或 HTTP 相关导入
    assert "fastapi" not in source
    assert "starlette" not in source
    assert "HTTPException" not in source


# ── 错误码注册表 ─────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_registry_accepts_valid_format() -> None:
    """注册表接受合法格式的错误码."""

    registry = ErrorCodeRegistry()
    registry.register(
        "USER.NOT_FOUND",
        404,
        meaning="用户不存在",
        scenario="按 ID 查询用户但未找到",
    )
    assert "USER.NOT_FOUND" in registry
    assert len(registry) == 1


@pytest.mark.g1
@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        "user.not_found",  # 小写
        "USER.NOT FOUND",  # 空格
        "USER.NOT-FOUND",  # 连字符
        "USERNOTFOUND",  # 无点分隔
        "USER.",  # 缺少 REASON
        ".NOT_FOUND",  # 缺少 MODULE
        "1USER.NOT_FOUND",  # MODULE 数字开头
        "USER.1NOT_FOUND",  # REASON 数字开头
        "US ER.NOT_FOUND",  # MODULE 含空格
        "",  # 空字符串
        "USER.NOT_FOUND.EXTRA",  # 三段
    ],
)
def test_registry_rejects_invalid_format(code: str) -> None:
    """注册表拒绝非法格式的错误码（SPEC 5.5 / 10.2）."""

    registry = ErrorCodeRegistry()
    with pytest.raises(InvalidErrorCodeFormatError):
        registry.register(code, 400, meaning="x", scenario="y")


@pytest.mark.g1
@pytest.mark.unit
def test_registry_rejects_duplicate() -> None:
    """注册表拒绝重复注册（SPEC 10.2: 全局唯一）."""

    registry = ErrorCodeRegistry()
    registry.register("USER.DUPLICATE", 409, meaning="x", scenario="y")
    with pytest.raises(DuplicateErrorCodeError):
        registry.register("USER.DUPLICATE", 409, meaning="x", scenario="y")


@pytest.mark.g1
@pytest.mark.unit
def test_registry_metadata_completeness() -> None:
    """每个错误码元数据含含义/HTTP 状态码/适用场景（SPEC 10.2）."""

    registry = ErrorCodeRegistry()
    registry.register(
        "USER.BANNED",
        403,
        meaning="用户已被封禁",
        scenario="用户状态为禁用时拒绝操作",
    )

    metadata = registry.get("USER.BANNED")
    assert metadata is not None
    assert metadata.code == "USER.BANNED"
    assert metadata.http_status == 403
    assert metadata.meaning == "用户已被封禁"
    assert metadata.scenario == "用户状态为禁用时拒绝操作"


@pytest.mark.g1
@pytest.mark.unit
def test_registry_get_returns_none_for_unregistered() -> None:
    """未注册的错误码 get 返回 None."""

    registry = ErrorCodeRegistry()
    assert registry.get("USER.NEVER_REGISTERED") is None


@pytest.mark.g1
@pytest.mark.unit
def test_registry_codes_is_readonly() -> None:
    """注册表 codes 属性返回只读视图."""

    registry = ErrorCodeRegistry()
    registry.register("TEST.ONE", 400, meaning="x", scenario="y")
    with pytest.raises(TypeError):
        registry.codes["TEST.ONE"] = None  # type: ignore[index]


@pytest.mark.g1
@pytest.mark.unit
def test_default_registry_has_framework_codes() -> None:
    """默认注册表包含所有框架级错误码."""

    expected_codes = {
        "APPLICATION.ERROR",
        "PARAMETER.INVALID",
        "VALIDATION.FAILED",
        "AUTH.UNAUTHENTICATED",
        "AUTH.FORBIDDEN",
        "COMMON.NOT_FOUND",
        "COMMON.CONFLICT",
        "SYSTEM.INTERNAL",
        "DB.UNIQUE_VIOLATION",
        "DB.CONNECTION_ERROR",
    }
    for code in expected_codes:
        assert code in default_registry
        metadata = default_registry.get(code)
        assert metadata is not None
        assert metadata.http_status > 0
        assert len(metadata.meaning) > 0
        assert len(metadata.scenario) > 0


@pytest.mark.g1
@pytest.mark.unit
def test_registry_separates_code_from_display_text() -> None:
    """错误码与展示文案分离（SPEC 10.2）.

    注册表存储元数据（含义/场景），但不存储面向终端用户的展示文案。
    展示文案由异常处理器在 API 边界动态生成。
    """

    registry = ErrorCodeRegistry()
    registry.register(
        "USER.DUPLICATE_EMAIL",
        409,
        meaning="邮箱已被注册",
        scenario="注册时邮箱已被其他用户占用",
    )

    metadata = registry.get("USER.DUPLICATE_EMAIL")
    assert metadata is not None
    # 元数据字段不含 title、detail 等展示文案字段
    assert not hasattr(metadata, "title")
    assert not hasattr(metadata, "detail")
    assert not hasattr(metadata, "message")


# ── ASGI 应用 problem+json 响应测试 ──────────────────────────────────────


def _create_test_app() -> FastAPI:
    """创建带异常处理器的测试应用（不连接数据库）.

    挂载临时路由以触发各类异常，验证 problem+json 响应结构。
    """

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    # ── 临时路由：触发各种 ApplicationError 子类 ──

    @app.get("/test/parameter-error")
    async def raise_parameter_error() -> None:
        raise ParameterError("参数格式不合法")

    @app.get("/test/validation-error")
    async def raise_validation_error() -> None:
        raise ValidationError(
            "字段校验失败",
            errors=[
                FieldError(
                    field="username",
                    reason="too_short",
                    message="用户名至少 3 个字符",
                ),
                FieldError(
                    field="email",
                    reason="invalid_format",
                    message="邮箱格式不正确",
                ),
            ],
        )

    @app.get("/test/authentication-error")
    async def raise_authentication_error() -> None:
        raise AuthenticationError("未提供认证凭证")

    @app.get("/test/authorization-error")
    async def raise_authorization_error() -> None:
        raise AuthorizationError("无权执行此操作")

    @app.get("/test/not-found-error")
    async def raise_not_found_error() -> None:
        raise NotFoundError("资源不存在")

    @app.get("/test/conflict-error")
    async def raise_conflict_error() -> None:
        raise ConflictError("状态冲突")

    @app.get("/test/unique-violation")
    async def raise_unique_violation() -> None:
        raise UniqueViolationError("唯一约束冲突: 用户名已存在")

    @app.get("/test/db-connection-error")
    async def raise_db_connection_error() -> None:
        raise DatabaseConnectionError("无法连接数据库")

    @app.get("/test/unhandled")
    async def raise_unhandled() -> None:
        raise RuntimeError("内部意外错误: secret_path/C:\\code/internal")

    # ── 临时路由：触发 FastAPI RequestValidationError ──

    class _TestSchema(BaseModel):
        model_config = {"extra": "forbid"}
        name: str
        age: int

    @app.post("/test/pydantic-validation")
    async def pydantic_validation(data: _TestSchema) -> dict[str, str]:
        return {"name": data.name}

    return app


def _assert_problem_structure(
    data: dict[str, Any],
    expected_status: int,
) -> None:
    """断言 problem+json 固定字段结构."""

    required_fields = {
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "code",
        "request_id",
    }
    assert set(data.keys()) >= required_fields
    assert data["status"] == expected_status


@pytest.mark.g1
@pytest.mark.unit
def test_business_error_content_type_is_problem_json() -> None:
    """业务错误响应 Content-Type 为 application/problem+json（SPEC 9.3）."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.get("/test/not-found-error")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.g1
@pytest.mark.unit
def test_business_error_has_all_fixed_fields_and_urn_type() -> None:
    """业务错误固定含 7 字段且 type 符合 urn 规则（SPEC 9.3）."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.get("/test/not-found-error")

    data = response.json()
    _assert_problem_structure(data, 404)

    # type 符合 urn:apex:problem:<小写错误码>
    assert data["type"] == "urn:apex:problem:common.not_found"
    assert data["code"] == "COMMON.NOT_FOUND"
    assert data["title"]  # 非空
    assert data["detail"]  # 非空
    assert data["instance"] == "/test/not-found-error"


@pytest.mark.g1
@pytest.mark.unit
def test_request_id_consistency_between_header_and_body() -> None:
    """request_id 与响应头一致（SPEC 9.3 / 9.5）."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.get(
            "/test/not-found-error",
            headers={"X-Request-ID": "test-req-123"},
        )

    assert response.headers["X-Request-ID"] == "test-req-123"
    data = response.json()
    assert data["request_id"] == "test-req-123"


@pytest.mark.g1
@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "expected_status", "expected_code"),
    [
        ("/test/parameter-error", 400, "PARAMETER.INVALID"),
        ("/test/authentication-error", 401, "AUTH.UNAUTHENTICATED"),
        ("/test/authorization-error", 403, "AUTH.FORBIDDEN"),
        ("/test/not-found-error", 404, "COMMON.NOT_FOUND"),
        ("/test/conflict-error", 409, "COMMON.CONFLICT"),
        ("/test/unique-violation", 409, "DB.UNIQUE_VIOLATION"),
        ("/test/db-connection-error", 503, "DB.CONNECTION_ERROR"),
    ],
)
def test_application_error_mappings(
    path: str,
    expected_status: int,
    expected_code: str,
) -> None:
    """各 ApplicationError 子类映射到正确状态码和错误码（SPEC 10.1）."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["code"] == expected_code
    assert data["status"] == expected_status
    # 业务错误 type 为 urn:apex:problem:<小写错误码>
    assert data["type"] == f"urn:apex:problem:{expected_code.lower()}"


@pytest.mark.g1
@pytest.mark.unit
def test_business_errors_not_http_200() -> None:
    """业务错误不被包装为 HTTP 200（SPEC 9.3）."""

    app = _create_test_app()
    error_paths = [
        "/test/parameter-error",
        "/test/authentication-error",
        "/test/authorization-error",
        "/test/not-found-error",
        "/test/conflict-error",
        "/test/unique-violation",
        "/test/db-connection-error",
    ]
    with TestClient(app) as client:
        for path in error_paths:
            response = client.get(path)
            assert response.status_code != 200, f"{path} 返回了 200"
            assert response.status_code >= 400


# ── 字段校验错误 422 ─────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_application_validation_error_returns_422_with_errors() -> None:
    """ApplicationError 的 ValidationError 返回 422 且含 errors 数组."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.get("/test/validation-error")

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()

    # 固定字段
    _assert_problem_structure(data, 422)
    assert data["code"] == "VALIDATION.FAILED"
    # type 为 urn（ApplicationError 子类）
    assert data["type"] == "urn:apex:problem:validation.failed"

    # errors 数组
    assert "errors" in data
    errors = data["errors"]
    assert len(errors) == 2
    for err in errors:
        assert set(err.keys()) == {"field", "reason", "message"}
    assert errors[0]["field"] == "username"
    assert errors[0]["reason"] == "too_short"


@pytest.mark.g1
@pytest.mark.unit
def test_fastapi_validation_error_returns_422_with_errors() -> None:
    """FastAPI RequestValidationError 返回 422 problem+json 含 errors（SPEC 9.3）."""

    app = _create_test_app()
    with TestClient(app) as client:
        # 发送缺少必填字段的请求体
        response = client.post(
            "/test/pydantic-validation",
            json={"name": "test"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()

    # 固定字段
    _assert_problem_structure(data, 422)
    assert data["code"] == "VALIDATION.FAILED"
    # 框架级验证错误 type 为 about:blank
    assert data["type"] == "about:blank"

    # errors 数组，元素含 field/reason/message
    assert "errors" in data
    errors = data["errors"]
    assert len(errors) >= 1
    for err in errors:
        assert "field" in err
        assert "reason" in err
        assert "message" in err


@pytest.mark.g1
@pytest.mark.unit
def test_fastapi_validation_error_unknown_field_rejected() -> None:
    """extra=forbid 的 Schema 对未知字段返回 422."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.post(
            "/test/pydantic-validation",
            json={"name": "test", "age": 18, "unknown": "field"},
        )

    assert response.status_code == 422
    data = response.json()
    assert "errors" in data


# ── 未处理异常 500 ───────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_unhandled_exception_returns_500_problem_json() -> None:
    """未处理异常返回 500 problem+json（SPEC 10.1 / 23.3）.

    Starlette 1.0 将 ``Exception`` 处理器路由到 ``ServerErrorMiddleware``，
    该中间件在发送响应后仍重新抛出异常，因此 TestClient 需要
    ``raise_server_exceptions=False`` 才能接收 500 响应。
    """

    app = _create_test_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/unhandled")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()

    _assert_problem_structure(data, 500)
    assert data["code"] == "SYSTEM.INTERNAL"
    # 框架级错误 type 为 about:blank
    assert data["type"] == "about:blank"


@pytest.mark.g1
@pytest.mark.unit
def test_unhandled_exception_no_stack_trace_in_response() -> None:
    """500 响应不含堆栈与内部路径（SPEC 23.3）."""

    app = _create_test_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/unhandled")

    body_text = response.text
    # 不含堆栈相关内容
    assert "Traceback" not in body_text
    assert "RuntimeError" not in body_text
    # 不含异常消息中的内部路径与敏感细节（SPEC 23.3）
    assert "secret_path" not in body_text
    assert "C:\\" not in body_text


@pytest.mark.g1
@pytest.mark.unit
def test_unhandled_exception_logs_full_stack_trace() -> None:
    """服务端日志含完整堆栈（SPEC 24.1）."""

    import structlog

    app = _create_test_app()

    with (
        structlog.testing.capture_logs() as cap_logs,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        client.get("/test/unhandled")

    # 应有且仅有一条 error 级别日志记录未处理异常
    error_logs = [log for log in cap_logs if log["log_level"] == "error"]
    assert len(error_logs) == 1

    log_entry = error_logs[0]
    assert log_entry["event"] == "未处理异常"
    assert log_entry["error_type"] == "RuntimeError"
    # exc_info 包含完整异常信息
    assert "exc_info" in log_entry
    exc_info = log_entry["exc_info"]
    assert exc_info is not None
    assert exc_info[0] is RuntimeError


@pytest.mark.g1
@pytest.mark.unit
def test_unhandled_exception_not_logged_twice() -> None:
    """未处理异常不重复记录（SPEC 24.1）."""

    import structlog

    app = _create_test_app()

    with (
        structlog.testing.capture_logs() as cap_logs,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        client.get("/test/unhandled")

    # 过滤出异常日志（非 request 日志）
    error_logs = [log for log in cap_logs if log.get("event") == "未处理异常"]
    assert len(error_logs) == 1, "未处理异常被重复记录"


# ── HTTPException 映射 ──────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_http_404_returns_problem_json() -> None:
    """未知路由返回 404 problem+json（SPEC 9.3）."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.get("/test/nonexistent-path")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    _assert_problem_structure(data, 404)
    assert data["type"] == "about:blank"
    assert data["code"] == "HTTP.NOT_FOUND"


@pytest.mark.g1
@pytest.mark.unit
def test_http_405_returns_problem_json() -> None:
    """不支持的方法返回 405 problem+json."""

    app = _create_test_app()
    with TestClient(app) as client:
        response = client.delete("/test/not-found-error")

    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"
    data = response.json()
    assert data["type"] == "about:blank"
    assert data["code"] == "HTTP.METHOD_NOT_ALLOWED"


# ── UniqueViolationError 继承验证 ─────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_unique_violation_is_conflict_and_application_error() -> None:
    """UniqueViolationError 同时是 ConflictError 和 ApplicationError.

    SPEC 10.1: 异常层级正确区分状态冲突。
    """

    exc = UniqueViolationError("重复")
    assert isinstance(exc, ConflictError)
    assert isinstance(exc, ApplicationError)
    assert exc.code == "DB.UNIQUE_VIOLATION"


@pytest.mark.g1
@pytest.mark.unit
def test_database_connection_error_is_system_and_application_error() -> None:
    """DatabaseConnectionError 同时是 SystemError 和 ApplicationError."""

    exc = DatabaseConnectionError("断开")
    assert isinstance(exc, SystemError)
    assert isinstance(exc, ApplicationError)
    assert exc.code == "DB.CONNECTION_ERROR"
