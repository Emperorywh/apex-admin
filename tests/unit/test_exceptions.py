"""异常体系与错误码单元测试（SPEC §10.1、§10.2、§9.3）。

覆盖验收条件：
- 六种异常基类
- 每个异常携带 MODULE.REASON 格式的稳定错误码
- 错误码格式校验、框架级 vs 业务级判断、RFC 9457 type URI 构建
- IntegrityConstraintError 继承 ConflictError、DatabaseOperationError 继承 SystemError
- 数据库约束错误映射为冲突/参数错误及稳定错误码
- 字段校验错误 FieldError
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DatabaseOperationError,
    FieldError,
    IntegrityConstraintError,
    NotFoundError,
    ParameterError,
    SystemError,
    build_problem_type,
    is_framework_code,
    is_valid_error_code,
)
from app.infrastructure.database.exceptions import translate_db_exception

pytestmark = [pytest.mark.unit, pytest.mark.g1]


# ---------------------------------------------------------------------------
# 异常基类：六种异常类型（SPEC §10.1）
# ---------------------------------------------------------------------------


class TestExceptionTypes:
    """验证六种异常基类的默认错误码、HTTP 状态码和标题。"""

    def test_parameter_error_defaults(self) -> None:
        """ParameterError → HTTP 400，错误码 APP.PARAMETER。"""
        exc = ParameterError("参数不合法")
        assert exc.code == "APP.PARAMETER"
        assert exc.http_status == 400
        assert exc.title == "参数错误"
        assert exc.detail == "参数不合法"
        assert exc.errors is None

    def test_authentication_error_defaults(self) -> None:
        """AuthenticationError → HTTP 401，错误码 APP.UNAUTHENTICATED。"""
        exc = AuthenticationError("请先登录")
        assert exc.code == "APP.UNAUTHENTICATED"
        assert exc.http_status == 401
        assert exc.title == "未认证"

    def test_authorization_error_defaults(self) -> None:
        """AuthorizationError → HTTP 403，错误码 APP.FORBIDDEN。"""
        exc = AuthorizationError("无权限")
        assert exc.code == "APP.FORBIDDEN"
        assert exc.http_status == 403
        assert exc.title == "禁止访问"

    def test_not_found_error_defaults(self) -> None:
        """NotFoundError → HTTP 404，错误码 APP.NOT_FOUND。"""
        exc = NotFoundError("资源不存在")
        assert exc.code == "APP.NOT_FOUND"
        assert exc.http_status == 404
        assert exc.title == "资源不存在"

    def test_conflict_error_defaults(self) -> None:
        """ConflictError → HTTP 409，错误码 APP.CONFLICT。"""
        exc = ConflictError("状态冲突")
        assert exc.code == "APP.CONFLICT"
        assert exc.http_status == 409
        assert exc.title == "状态冲突"

    def test_system_error_defaults(self) -> None:
        """SystemError → HTTP 500，错误码 APP.SYSTEM_ERROR。"""
        exc = SystemError("系统错误")
        assert exc.code == "APP.SYSTEM_ERROR"
        assert exc.http_status == 500
        assert exc.title == "系统错误"


class TestExceptionInheritance:
    """验证所有异常类型继承 AppError。"""

    @pytest.mark.parametrize(
        ("exc_type", "detail"),
        [
            (ParameterError, "参数错误"),
            (AuthenticationError, "认证错误"),
            (AuthorizationError, "授权错误"),
            (NotFoundError, "不存在"),
            (ConflictError, "冲突"),
            (SystemError, "系统错误"),
            (IntegrityConstraintError, "约束冲突"),
            (DatabaseOperationError, "操作错误"),
        ],
    )
    def test_all_are_app_error(self, exc_type: type[AppError], detail: str) -> None:
        """所有异常类型都是 AppError 的子类。"""
        exc = exc_type(detail)
        assert isinstance(exc, AppError)

    def test_integrity_constraint_error_is_conflict(self) -> None:
        """IntegrityConstraintError 继承 ConflictError（SPEC §10.1：冲突）。"""
        exc = IntegrityConstraintError("唯一约束冲突")
        assert isinstance(exc, ConflictError)
        assert isinstance(exc, AppError)
        assert exc.http_status == 409

    def test_database_operation_error_is_system(self) -> None:
        """DatabaseOperationError 继承 SystemError（SPEC §10.1：系统错误）。"""
        exc = DatabaseOperationError("连接失败")
        assert isinstance(exc, SystemError)
        assert isinstance(exc, AppError)
        assert exc.http_status == 500


class TestCustomBusinessCode:
    """验证业务模块可传入自定义 MODULE.REASON 错误码。"""

    def test_custom_code_overrides_default(self) -> None:
        """NotFoundError 传入业务 code 时使用业务 code。"""
        exc = NotFoundError("用户不存在", code="USER.NOT_FOUND")
        assert exc.code == "USER.NOT_FOUND"
        assert exc.http_status == 404
        assert exc.detail == "用户不存在"

    def test_custom_code_with_conflict(self) -> None:
        """ConflictError 传入业务 code 时使用业务 code。"""
        exc = ConflictError("邮箱已被使用", code="USER.EMAIL_DUPLICATE")
        assert exc.code == "USER.EMAIL_DUPLICATE"
        assert exc.http_status == 409

    def test_custom_code_with_parameter(self) -> None:
        """ParameterError 传入业务 code 时使用业务 code。"""
        exc = ParameterError("邮箱格式错误", code="USER.INVALID_EMAIL")
        assert exc.code == "USER.INVALID_EMAIL"
        assert exc.http_status == 400


class TestFieldErrors:
    """验证 ParameterError 携带字段校验错误列表（SPEC §9.3）。"""

    def test_parameter_error_with_field_errors(self) -> None:
        """ParameterError 可携带 errors 数组。"""
        errors = [
            FieldError(field="email", reason="duplicate", message="邮箱已被使用"),
            FieldError(field="username", reason="too_short", message="用户名至少 3 个字符"),
        ]
        exc = ParameterError("字段校验失败", errors=errors)
        assert exc.errors is not None
        assert len(exc.errors) == 2
        assert exc.errors[0].field == "email"
        assert exc.errors[0].reason == "duplicate"
        assert exc.errors[0].message == "邮箱已被使用"

    def test_field_error_attributes(self) -> None:
        """FieldError 包含 field、reason、message 三项。"""
        fe = FieldError(field="name", reason="missing", message="字段必填")
        assert fe.field == "name"
        assert fe.reason == "missing"
        assert fe.message == "字段必填"

    def test_field_error_rejects_extra_fields(self) -> None:
        """FieldError 拒绝未知字段（extra=forbid）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FieldError(field="name", reason="missing", message="msg", extra_field="bad")  # type: ignore[call-arg]


class TestEmptyDetailFallback:
    """验证 detail 为空时回退到 title。"""

    def test_empty_detail_falls_back_to_title(self) -> None:
        """detail 传空字符串时使用 title 作为 detail。"""
        exc = ParameterError()
        assert exc.detail == "参数错误"


# ---------------------------------------------------------------------------
# 错误码格式与 type URI（SPEC §10.2、§9.3）
# ---------------------------------------------------------------------------


class TestErrorCodeValidation:
    """验证错误码格式校验。"""

    @pytest.mark.parametrize(
        "code",
        [
            "USER.NOT_FOUND",
            "APP.PARAMETER",
            "DB.INTEGRITY_CONSTRAINT",
            "ROLE.ALREADY_EXISTS",
            "A.B",
            "MODULE.REASON_WITH_NUMBERS_123",
        ],
    )
    def test_valid_codes(self, code: str) -> None:
        """合法 MODULE.REASON 格式通过校验。"""
        assert is_valid_error_code(code) is True

    @pytest.mark.parametrize(
        "code",
        [
            "invalid",
            "user.not_found",  # 小写不允许
            "USER.not_found",  # REASON 部分小写不允许
            "user.NOT_FOUND",  # MODULE 部分小写不允许
            "USER.NOT_FOUND.EXTRA",  # 三段不允许
            ".NOT_FOUND",  # 缺少 MODULE
            "USER.",  # 缺少 REASON
            "1USER.NOT_FOUND",  # MODULE 以数字开头
            "USER.1NOT_FOUND",  # REASON 以数字开头
            "",  # 空字符串
        ],
    )
    def test_invalid_codes(self, code: str) -> None:
        """非法格式被拒绝。"""
        assert is_valid_error_code(code) is False


class TestFrameworkCodeDetection:
    """验证框架级 vs 业务级错误码判断。"""

    @pytest.mark.parametrize(
        "code",
        ["APP.PARAMETER", "APP.UNAUTHENTICATED", "DB.INTEGRITY_CONSTRAINT", "DB.OPERATION_ERROR"],
    )
    def test_framework_codes(self, code: str) -> None:
        """APP 和 DB 前缀为框架/基础设施层代码。"""
        assert is_framework_code(code) is True

    @pytest.mark.parametrize(
        "code",
        ["USER.NOT_FOUND", "ROLE.ALREADY_EXISTS", "CONFIG.KEY_DUPLICATE", "FILE.NOT_READY"],
    )
    def test_business_codes(self, code: str) -> None:
        """业务模块前缀不是框架代码。"""
        assert is_framework_code(code) is False


class TestProblemTypeBuilder:
    """验证 RFC 9457 type URI 构建（SPEC §9.3）。"""

    def test_business_code_gets_urn(self) -> None:
        """业务错误码 → urn:apex:problem:<小写错误码>。"""
        assert build_problem_type("USER.NOT_FOUND") == "urn:apex:problem:user.not_found"
        assert build_problem_type("ROLE.ALREADY_EXISTS") == "urn:apex:problem:role.already_exists"

    def test_framework_code_gets_about_blank(self) -> None:
        """框架级错误码 → about:blank。"""
        assert build_problem_type("APP.PARAMETER") == "about:blank"
        assert build_problem_type("DB.INTEGRITY_CONSTRAINT") == "about:blank"


# ---------------------------------------------------------------------------
# 数据库约束错误映射（SPEC §8.1、§10.1、§9.3）
# ---------------------------------------------------------------------------


class TestDatabaseConstraintMapping:
    """验证数据库完整性错误映射为冲突/参数错误及稳定错误码。"""

    def test_integrity_error_maps_to_conflict(self) -> None:
        """IntegrityError → IntegrityConstraintError（ConflictError 子类，409）。"""
        original = IntegrityError("stmt", {}, Exception("unique violation"))
        result = translate_db_exception(original)
        assert isinstance(result, IntegrityConstraintError)
        assert isinstance(result, ConflictError)
        assert result.http_status == 409

    def test_integrity_error_has_stable_code(self) -> None:
        """IntegrityConstraintError 携带稳定错误码 DB.INTEGRITY_CONSTRAINT。"""
        original = IntegrityError("stmt", {}, Exception("dup"))
        result = translate_db_exception(original)
        assert result.code == "DB.INTEGRITY_CONSTRAINT"

    def test_operational_error_maps_to_system(self) -> None:
        """OperationalError → DatabaseOperationError（SystemError 子类，500）。"""
        original = OperationalError("stmt", {}, Exception("connection refused"))
        result = translate_db_exception(original)
        assert isinstance(result, DatabaseOperationError)
        assert isinstance(result, SystemError)
        assert result.http_status == 500

    def test_operational_error_has_stable_code(self) -> None:
        """DatabaseOperationError 携带稳定错误码 DB.OPERATION_ERROR。"""
        original = OperationalError("stmt", {}, Exception("conn"))
        result = translate_db_exception(original)
        assert result.code == "DB.OPERATION_ERROR"

    def test_non_db_exception_unchanged(self) -> None:
        """非数据库异常原样返回。"""
        original = ValueError("not a db error")
        result = translate_db_exception(original)
        assert result is original

    def test_already_mapped_error_not_re_mapped(self) -> None:
        """已经是应用异常的不再被二次映射。"""
        app_error = IntegrityConstraintError("already mapped")
        result = translate_db_exception(app_error)
        assert result is app_error
