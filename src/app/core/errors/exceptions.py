"""异常层级 — SPEC 10.1.

应用异常与 HTTP 解耦：异常类不导入 FastAPI 或 HTTP 相关模块，
异常到 HTTP 响应的转换在 API 边界统一完成
（``app.api.exception_handlers``）。

SPEC 10.1 异常分类:
  - 参数错误（400）
  - 认证错误（401）
  - 授权错误（403）
  - 资源不存在（404）
  - 状态冲突（409）
  - 系统错误（500）

异常层级::

    ApplicationError（基类）
      ├── ParameterError（参数错误, 400）
      │   └── ValidationError（字段校验错误, 422）
      ├── AuthenticationError（认证错误, 401）
      ├── AuthorizationError（授权错误, 403）
      ├── NotFoundError（资源不存在, 404）
      ├── ConflictError（状态冲突, 409）
      │   └── UniqueViolationError（唯一约束冲突, 409）
      └── SystemError（系统错误, 500）
          └── DatabaseConnectionError（数据库连接错误, 503）

每个异常类携带稳定错误码 ``code``（SPEC 10.2），客户端据此做业务判断，
不依赖可变的展示文案。错误码格式为 ``<MODULE>.<REASON>``（SPEC 5.5），
仅大写字母、数字和下划线。

领域错误不依赖 FastAPI、ORM 或任何基础设施类型（SPEC 5.2 / 10.1）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldError:
    """字段校验错误项 — RFC 9457 errors 数组元素.

    SPEC 9.3: "数组元素固定包含 field、reason 和 message"。

    属性:
        field:   字段路径，如 ``"username"`` 或 ``"body.email"``。
        reason:  校验失败原因编码，如 ``"missing"``、``"value_error"``。
        message: 人类可读说明，仅供展示（SPEC 9.3）。
    """

    field: str
    reason: str
    message: str


class ApplicationError(Exception):
    """应用层基础异常 — 所有应用异常的基类.

    SPEC 10.2: 携带全局唯一的稳定错误码 ``code``，
    客户端据此做业务判断，不依赖可变的展示文案。

    此类不依赖 FastAPI、SQLAlchemy 或任何 HTTP/ORM 类型，
    确保领域错误与传输层解耦（SPEC 10.1: "领域错误与 HTTP 异常解耦"）。
    """

    code: str = "APPLICATION.ERROR"


class ParameterError(ApplicationError):
    """参数错误 — 客户端请求参数不合法（SPEC 10.1）.

    用于参数格式或基本值不符合要求的情况（非字段级校验）。
    HTTP 状态码 400。
    """

    code = "PARAMETER.INVALID"


class ValidationError(ParameterError):
    """字段校验错误 — 请求体字段未通过校验规则（SPEC 9.3）.

    HTTP 状态码 422。携带 ``field_errors`` 数组，
    每个元素包含 field/reason/message（SPEC 9.3）。
    """

    code = "VALIDATION.FAILED"

    def __init__(
        self,
        message: str,
        *,
        errors: list[FieldError],
    ) -> None:
        """初始化字段校验错误.

        参数:
            message: 总体错误说明，仅供展示。
            errors:  字段级错误列表，每个元素含 field/reason/message。
        """

        super().__init__(message)
        self.field_errors: list[FieldError] = errors


class AuthenticationError(ApplicationError):
    """认证错误 — 请求未通过身份验证（SPEC 10.1）.

    HTTP 状态码 401。
    G2 阶段实现具体认证逻辑，G1 仅定义异常类型。
    """

    code = "AUTH.UNAUTHENTICATED"


class AuthorizationError(ApplicationError):
    """授权错误 — 已认证但无权执行操作（SPEC 10.1）.

    HTTP 状态码 403。
    G2 阶段实现具体授权逻辑，G1 仅定义异常类型。
    """

    code = "AUTH.FORBIDDEN"


class NotFoundError(ApplicationError):
    """资源不存在错误（SPEC 10.1）.

    HTTP 状态码 404。
    请求的资源标识在系统中不存在。
    """

    code = "COMMON.NOT_FOUND"


class ConflictError(ApplicationError):
    """状态冲突错误（SPEC 10.1）.

    HTTP 状态码 409。
    请求与当前资源状态冲突，无法完成操作。
    """

    code = "COMMON.CONFLICT"


class SystemError(ApplicationError):
    """系统错误 — 内部故障，非业务逻辑问题（SPEC 10.1）.

    HTTP 状态码 500。
    用于未处理的内部异常或系统级故障。
    """

    code = "SYSTEM.INTERNAL"


class UniqueViolationError(ConflictError):
    """唯一约束冲突 — 数据库唯一约束违反（SPEC 8.3 / 8.4 / 10.1）.

    HTTP 状态码 409。
    继承 ``ConflictError``，因为唯一约束冲突本质上是一种状态冲突。

    SPEC 8.3: "唯一性规则优先由数据库唯一约束保证"。
    SPEC 8.4: "冲突错误具有明确的业务错误码"。
    """

    code = "DB.UNIQUE_VIOLATION"


class DatabaseConnectionError(SystemError):
    """数据库连接错误 — 无法连接数据库或连接中断（SPEC 8.1 / 10.1）.

    HTTP 状态码 503。
    继承 ``SystemError``，因为连接故障属于系统级错误而非业务冲突。

    SPEC 6.1: "数据库暂时不可用只影响就绪状态"。
    """

    code = "DB.CONNECTION_ERROR"
