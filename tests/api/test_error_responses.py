"""API 错误响应测试（SPEC §9.3、§10.1、§28.4）。

覆盖验收条件：
- API 异常处理器将所有异常转为 RFC 9457 application/problem+json
- ProblemDetail 含 type、title、status、detail、instance、code、request_id
- 业务错误 type = urn:apex:problem:<小写错误码>；框架级错误使用 about:blank
- 字段校验错误额外含 errors 数组（field、reason、message）
- 未处理异常记录完整堆栈；生产响应不暴露内部细节
- 数据库约束错误映射为冲突/参数错误及稳定错误码
- 响应不得将业务错误包装为 HTTP 200
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from starlette.testclient import TestClient

from app.api.handlers import register_exception_handlers
from app.errors.base import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DatabaseOperationError,
    FieldError,
    IntegrityConstraintError,
    NotFoundError,
    ParameterError,
    SystemError,
)
from app.middleware.request_id import RequestIdMiddleware

pytestmark = [pytest.mark.api, pytest.mark.g1]


# ---------------------------------------------------------------------------
# 测试用请求体 Schema
# ---------------------------------------------------------------------------


class ItemCreate(BaseModel):
    """测试用请求体，含必填字段和约束。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=0, le=9999)


# ---------------------------------------------------------------------------
# 测试应用工厂
# ---------------------------------------------------------------------------


def _create_test_app() -> FastAPI:
    """创建包含异常处理器和测试路由的 FastAPI 应用。

    测试路由故意抛出各类异常，验证异常处理器的 RFC 9457 转换行为。
    """
    app = FastAPI()

    # 注册异常处理器和 Request ID 中间件（完整模拟生产环境）
    register_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    # ---- 框架级异常（使用默认 code）----

    @app.get("/errors/parameter")
    async def raise_parameter() -> None:
        raise ParameterError("参数不合法")

    @app.get("/errors/authentication")
    async def raise_authentication() -> None:
        raise AuthenticationError("请先登录")

    @app.get("/errors/authorization")
    async def raise_authorization() -> None:
        raise AuthorizationError("无权限")

    @app.get("/errors/not-found")
    async def raise_not_found() -> None:
        raise NotFoundError("资源不存在")

    @app.get("/errors/conflict")
    async def raise_conflict() -> None:
        raise ConflictError("状态冲突")

    @app.get("/errors/system")
    async def raise_system() -> None:
        raise SystemError("系统错误")

    # ---- 业务异常（传入自定义 MODULE.REASON code）----

    @app.get("/errors/business-not-found")
    async def raise_business_not_found() -> None:
        raise NotFoundError("用户不存在", code="USER.NOT_FOUND")

    @app.get("/errors/business-conflict")
    async def raise_business_conflict() -> None:
        raise ConflictError("邮箱已被使用", code="USER.EMAIL_DUPLICATE")

    @app.get("/errors/business-parameter")
    async def raise_business_parameter() -> None:
        raise ParameterError("邮箱格式错误", code="USER.INVALID_EMAIL")

    # ---- 数据库约束异常 ----

    @app.get("/errors/integrity")
    async def raise_integrity() -> None:
        raise IntegrityConstraintError("唯一约束冲突")

    @app.get("/errors/db-operation")
    async def raise_db_operation() -> None:
        raise DatabaseOperationError("连接失败")

    # ---- 字段校验错误 ----

    @app.get("/errors/field-errors")
    async def raise_field_errors() -> None:
        raise ParameterError(
            "字段校验失败",
            errors=[
                FieldError(field="email", reason="duplicate", message="邮箱已被使用"),
                FieldError(field="username", reason="too_short", message="用户名至少 3 个字符"),
            ],
        )

    # ---- Pydantic 请求体校验失败（框架自动触发）----

    @app.post("/errors/validation")
    async def validation_endpoint(item: ItemCreate) -> dict[str, str]:
        return {"name": item.name}

    # ---- 未处理异常 ----

    @app.get("/errors/unhandled")
    async def raise_unhandled() -> None:
        raise RuntimeError("内部错误：连接池已耗尽，SQL=SELECT * FROM users")

    return app


@pytest.fixture
def client() -> TestClient:
    """创建测试客户端。

    raise_server_exceptions=False 确保未处理异常返回 500 响应而非在客户端抛出，
    因为 FastAPI 将 Exception 处理器注册到 ServerErrorMiddleware（会 re-raise）。
    """
    return TestClient(_create_test_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# RFC 9457 结构验证（SPEC §9.3、§28.4）
# ---------------------------------------------------------------------------


class TestProblemDetailStructure:
    """验证每种异常类型的 RFC 9457 响应结构和 HTTP 状态码。"""

    @pytest.mark.parametrize(
        ("path", "expected_status", "expected_code", "expected_title"),
        [
            ("/errors/parameter", 400, "APP.PARAMETER", "参数错误"),
            ("/errors/authentication", 401, "APP.UNAUTHENTICATED", "未认证"),
            ("/errors/authorization", 403, "APP.FORBIDDEN", "禁止访问"),
            ("/errors/not-found", 404, "APP.NOT_FOUND", "资源不存在"),
            ("/errors/conflict", 409, "APP.CONFLICT", "状态冲突"),
            ("/errors/system", 500, "APP.SYSTEM_ERROR", "系统错误"),
        ],
    )
    def test_framework_exception_response(
        self,
        client: TestClient,
        path: str,
        expected_status: int,
        expected_code: str,
        expected_title: str,
    ) -> None:
        """框架级异常产生正确的 RFC 9457 响应和状态码。"""
        response = client.get(path)

        # 响应不得将业务错误包装为 HTTP 200（SPEC §9.3）
        assert response.status_code == expected_status
        assert response.status_code != 200

        # Content-Type 固定为 application/problem+json（SPEC §9.3）
        assert response.headers["content-type"] == "application/problem+json"

        body: dict[str, Any] = response.json()

        # 全部固定字段存在（SPEC §9.3）
        required_fields = {"type", "title", "status", "detail", "instance", "code", "request_id"}
        assert required_fields.issubset(body.keys())

        assert body["type"] == "about:blank"
        assert body["title"] == expected_title
        assert body["status"] == expected_status
        assert body["code"] == expected_code
        assert body["instance"] == path
        assert body["request_id"]  # 非空

    def test_response_has_x_request_id_header(self, client: TestClient) -> None:
        """异常响应也携带 X-Request-ID 响应头。"""
        response = client.get("/errors/parameter", headers={"X-Request-ID": "test-req-123"})
        assert response.headers["X-Request-ID"] == "test-req-123"

        body = response.json()
        assert body["request_id"] == "test-req-123"


class TestBusinessErrorType:
    """验证业务错误的 type 使用 urn:apex:problem（SPEC §9.3）。"""

    def test_business_not_found_type(self, client: TestClient) -> None:
        """业务 NotFoundError 的 type = urn:apex:problem:user.not_found。"""
        response = client.get("/errors/business-not-found")
        body = response.json()

        assert response.status_code == 404
        assert body["type"] == "urn:apex:problem:user.not_found"
        assert body["code"] == "USER.NOT_FOUND"

    def test_business_conflict_type(self, client: TestClient) -> None:
        """业务 ConflictError 的 type = urn:apex:problem:user.email_duplicate。"""
        response = client.get("/errors/business-conflict")
        body = response.json()

        assert response.status_code == 409
        assert body["type"] == "urn:apex:problem:user.email_duplicate"
        assert body["code"] == "USER.EMAIL_DUPLICATE"

    def test_business_parameter_type(self, client: TestClient) -> None:
        """业务 ParameterError 的 type = urn:apex:problem:user.invalid_email。"""
        response = client.get("/errors/business-parameter")
        body = response.json()

        assert response.status_code == 400
        assert body["type"] == "urn:apex:problem:user.invalid_email"
        assert body["code"] == "USER.INVALID_EMAIL"

    def test_framework_error_type_is_about_blank(self, client: TestClient) -> None:
        """框架级错误的 type = about:blank。"""
        response = client.get("/errors/parameter")
        body = response.json()
        assert body["type"] == "about:blank"


# ---------------------------------------------------------------------------
# 字段校验错误（SPEC §9.3）
# ---------------------------------------------------------------------------


class TestFieldValidationErrors:
    """验证字段校验错误响应含 errors 数组（SPEC §9.3）。"""

    def test_app_error_with_field_errors(self, client: TestClient) -> None:
        """AppError 携带的 errors 数组完整出现在响应中。"""
        response = client.get("/errors/field-errors")
        body = response.json()

        assert response.status_code == 400
        assert "errors" in body
        assert len(body["errors"]) == 2

        first = body["errors"][0]
        assert first["field"] == "email"
        assert first["reason"] == "duplicate"
        assert first["message"] == "邮箱已被使用"

        second = body["errors"][1]
        assert second["field"] == "username"
        assert second["reason"] == "too_short"
        assert second["message"] == "用户名至少 3 个字符"

    def test_pydantic_validation_has_errors_array(self, client: TestClient) -> None:
        """Pydantic 请求体校验失败的响应含 errors 数组。"""
        # name 缺失、quantity 超范围、body 含未知字段
        response = client.post(
            "/errors/validation",
            json={"quantity": -1, "extra_field": "bad"},
        )
        body = response.json()

        assert response.status_code == 422
        assert body["type"] == "about:blank"
        assert body["code"] == "APP.PARAMETER"
        assert "errors" in body
        assert len(body["errors"]) >= 1

        # 每个 error 元素含 field、reason、message
        for err in body["errors"]:
            assert "field" in err
            assert "reason" in err
            assert "message" in err

    def test_no_errors_array_when_none(self, client: TestClient) -> None:
        """无字段校验错误时 errors 不出现在响应中。"""
        response = client.get("/errors/parameter")
        body = response.json()
        assert "errors" not in body


# ---------------------------------------------------------------------------
# 未处理异常（SPEC §10.1、§23.3）
# ---------------------------------------------------------------------------


class TestUnhandledException:
    """验证未处理异常的响应不暴露内部细节。"""

    def test_unhandled_returns_500(self, client: TestClient) -> None:
        """未处理异常返回 HTTP 500。"""
        response = client.get("/errors/unhandled")
        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"

    def test_unhandled_does_not_expose_internals(self, client: TestClient) -> None:
        """未处理异常响应不暴露内部路径、SQL、配置和调用栈（SPEC §23.3）。"""
        response = client.get("/errors/unhandled")
        body = response.json()

        assert body["type"] == "about:blank"
        assert body["title"] == "系统错误"
        assert body["status"] == 500
        assert body["code"] == "APP.SYSTEM_ERROR"

        # detail 不含内部信息
        detail = body["detail"]
        assert "连接池" not in detail
        assert "SQL" not in detail
        assert "SELECT" not in detail
        assert "RuntimeError" not in detail

    def test_unhandled_has_request_id(self, client: TestClient) -> None:
        """未处理异常响应含 request_id。"""
        response = client.get("/errors/unhandled", headers={"X-Request-ID": "err-req-456"})
        body = response.json()
        assert body["request_id"] == "err-req-456"


# ---------------------------------------------------------------------------
# 数据库约束映射（SPEC §8.1、§10.1）
# ---------------------------------------------------------------------------


class TestDatabaseConstraintResponse:
    """验证数据库约束错误映射为冲突/参数错误及稳定错误码。"""

    def test_integrity_constraint_error_response(self, client: TestClient) -> None:
        """IntegrityConstraintError → 409，type=about:blank，code=DB.INTEGRITY_CONSTRAINT。"""
        response = client.get("/errors/integrity")
        body = response.json()

        assert response.status_code == 409
        assert body["type"] == "about:blank"
        assert body["code"] == "DB.INTEGRITY_CONSTRAINT"
        assert body["status"] == 409

    def test_database_operation_error_response(self, client: TestClient) -> None:
        """DatabaseOperationError → 500，type=about:blank，code=DB.OPERATION_ERROR。"""
        response = client.get("/errors/db-operation")
        body = response.json()

        assert response.status_code == 500
        assert body["type"] == "about:blank"
        assert body["code"] == "DB.OPERATION_ERROR"
        assert body["status"] == 500


# ---------------------------------------------------------------------------
# HTTP 200 包装验证（SPEC §9.3）
# ---------------------------------------------------------------------------


class TestNoHttp200Wrapping:
    """验证响应不得将业务错误包装为 HTTP 200（SPEC §9.3）。"""

    @pytest.mark.parametrize(
        "path",
        [
            "/errors/parameter",
            "/errors/authentication",
            "/errors/authorization",
            "/errors/not-found",
            "/errors/conflict",
            "/errors/system",
            "/errors/business-not-found",
            "/errors/business-conflict",
            "/errors/integrity",
            "/errors/db-operation",
            "/errors/unhandled",
        ],
    )
    def test_no_error_returns_200(self, client: TestClient, path: str) -> None:
        """所有错误路由不返回 200。"""
        response = client.get(path)
        assert response.status_code != 200


# ---------------------------------------------------------------------------
# 404 路由不存在（框架级 HTTP 异常）
# ---------------------------------------------------------------------------


class TestRouteNotFound:
    """验证路由不存在也返回 RFC 9457 格式。"""

    def test_route_not_found(self, client: TestClient) -> None:
        """不存在的路由返回 404 RFC 9457 ProblemDetail。"""
        response = client.get("/errors/nonexistent-route")
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 404
        assert "code" in body
        assert "request_id" in body


# ---------------------------------------------------------------------------
# 方法不允许（405）
# ---------------------------------------------------------------------------


class TestMethodNotAllowed:
    """验证方法不匹配返回 RFC 9457 格式。"""

    def test_method_not_allowed(self, client: TestClient) -> None:
        """对 GET 路由使用 POST 返回 405 RFC 9457 ProblemDetail。"""
        response = client.post("/errors/parameter")
        assert response.status_code == 405
        assert response.headers["content-type"] == "application/problem+json"

        body = response.json()
        assert body["type"] == "about:blank"
        assert body["status"] == 405
        assert "code" in body
