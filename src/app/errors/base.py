"""应用异常基类与类型体系（SPEC §10.1）。

定义稳定的应用级异常类型，将底层技术异常（数据库、ORM、外部服务）
与业务逻辑解耦。所有对外暴露的异常使用稳定的错误码，不泄露
SQLAlchemy、psycopg 或 SQL 细节。

异常层次（SPEC §10.1：区分参数错误、认证错误、授权错误、资源不存在、状态冲突和系统错误）::

    AppError（基类）
    ├── ParameterError          → HTTP 400  参数错误
    ├── AuthenticationError     → HTTP 401  认证错误
    ├── AuthorizationError      → HTTP 403  授权错误
    ├── NotFoundError           → HTTP 404  资源不存在
    ├── ConflictError           → HTTP 409  状态冲突
    │   └── IntegrityConstraintError  → HTTP 409  数据库完整性约束冲突
    └── SystemError             → HTTP 500  系统错误
        └── DatabaseOperationError    → HTTP 500  数据库操作错误

每个异常携带 ``<MODULE>.<REASON>`` 格式的稳定错误码（SPEC §10.2、§5.5）。
业务模块通过传入自定义 code 将异常标记为业务错误；
框架级错误使用默认 code（``APP`` 或 ``DB`` 前缀）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FieldError(BaseModel):
    """字段校验错误项（SPEC §9.3）。

    用于 ``ParameterError`` 和请求体校验错误中标记具体字段的问题。
    字段校验错误响应的 ``errors`` 数组元素固定包含此三项。

    Attributes:
        field: 字段路径，例如 ``"username"`` 或 ``"user.email"``
        reason: 错误原因编码，例如 ``"missing"`` 或 ``"value_error"``
        message: 供展示的错误说明，不保证稳定，不得用于业务判断
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str
    message: str


class AppError(Exception):
    """应用异常基类（SPEC §10.1）。

    所有应用级异常的公共基类。每个异常实例携带稳定的错误码，
    客户端业务判断只能使用错误码，不得依赖异常消息文本（SPEC §10.2）。

    子类通过类属性 ``default_code``、``default_http_status`` 和 ``default_title``
    声明该异常类型的默认框架级错误码、HTTP 状态码和 RFC 9457 标题。
    业务模块构造异常时传入自定义 ``code`` 将其标记为业务错误，
    此时 ``type`` 使用 ``urn:apex:problem:<小写错误码>``（SPEC §9.3）。

    类属性:
        default_code: 默认错误码，格式 ``<MODULE>.<REASON>``（SPEC §10.2、§5.5）
        default_http_status: 默认 HTTP 状态码
        default_title: 默认 RFC 9457 title

    实例属性:
        code: 稳定错误码；未传入自定义 code 时使用 default_code
        http_status: HTTP 状态码
        title: RFC 9457 title
        detail: 供展示的人类可读说明，不保证稳定，不得用于业务判断
        errors: 字段校验错误列表（可选，SPEC §9.3）
    """

    default_code: str = "APP.ERROR"
    default_http_status: int = 500
    default_title: str = "系统错误"

    def __init__(
        self,
        detail: str = "",
        *,
        code: str | None = None,
        http_status: int | None = None,
        title: str | None = None,
        errors: list[FieldError] | None = None,
    ) -> None:
        self.code = code if code is not None else self.default_code
        self.http_status = http_status if http_status is not None else self.default_http_status
        self.title = title if title is not None else self.default_title
        self.detail = detail if detail else self.default_title
        self.errors = errors
        super().__init__(self.detail)

    def __str__(self) -> str:
        return f"[{self.code}] {self.detail}"


class ParameterError(AppError):
    """参数错误（SPEC §10.1）。

    请求参数不合法或不满足业务约束，对应 HTTP 400。
    业务模块可传入自定义 ``code``（例如 ``USER.INVALID_EMAIL``）标记业务参数错误，
    并通过 ``errors`` 数组携带字段级校验详情（SPEC §9.3）。
    """

    default_code = "APP.PARAMETER"
    default_http_status = 400
    default_title = "参数错误"


class AuthenticationError(AppError):
    """认证错误（SPEC §10.1）。

    请求未认证或认证已过期，对应 HTTP 401。
    """

    default_code = "APP.UNAUTHENTICATED"
    default_http_status = 401
    default_title = "未认证"


class AuthorizationError(AppError):
    """授权错误（SPEC §10.1）。

    已认证但无权限访问目标资源，对应 HTTP 403。
    """

    default_code = "APP.FORBIDDEN"
    default_http_status = 403
    default_title = "禁止访问"


class NotFoundError(AppError):
    """资源不存在（SPEC §10.1）。

    请求的资源不存在，对应 HTTP 404。
    业务模块传入自定义 ``code`` 可区分具体资源类型，例如 ``USER.NOT_FOUND``。
    """

    default_code = "APP.NOT_FOUND"
    default_http_status = 404
    default_title = "资源不存在"


class ConflictError(AppError):
    """状态冲突（SPEC §10.1）。

    请求与当前资源状态冲突（如并发更新、唯一约束冲突），对应 HTTP 409。
    """

    default_code = "APP.CONFLICT"
    default_http_status = 409
    default_title = "状态冲突"


class SystemError(AppError):
    """系统错误（SPEC §10.1）。

    服务器内部错误，对应 HTTP 500。
    生产响应不暴露内部细节（SPEC §10.1、§23.3）。
    """

    default_code = "APP.SYSTEM_ERROR"
    default_http_status = 500
    default_title = "系统错误"


# ---------------------------------------------------------------------------
# 数据库专用异常（SPEC §8.1、§10.1）
#
# 这些异常由 Infrastructure 层的 translate_db_exception 映射产生，
# 继承对应的应用异常类别，携带数据库基础设施层稳定错误码（``DB`` 前缀）。
# ---------------------------------------------------------------------------


class IntegrityConstraintError(ConflictError):
    """数据库完整性约束冲突（SPEC §8.1、§10.1）。

    映射 SQLAlchemy ``IntegrityError``，覆盖唯一约束、外键约束和检查约束
    等违反场景。客户端应据此判断冲突原因并提示用户，不暴露底层约束名。

    继承 ``ConflictError``，HTTP 状态码 409，错误码 ``DB.INTEGRITY_CONSTRAINT``。
    """

    default_code = "DB.INTEGRITY_CONSTRAINT"
    default_http_status = 409
    default_title = "数据库完整性约束冲突"


class DatabaseOperationError(SystemError):
    """数据库操作错误（SPEC §8.1、§10.1）。

    映射 SQLAlchemy ``OperationalError``，覆盖连接失败、超时和死锁等
    操作级错误。此类错误通常需要重试或运维介入。

    继承 ``SystemError``，HTTP 状态码 500，错误码 ``DB.OPERATION_ERROR``。
    """

    default_code = "DB.OPERATION_ERROR"
    default_http_status = 500
    default_title = "数据库操作错误"
