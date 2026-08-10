"""错误处理核心包 — 异常层级与错误码注册表（SPEC 10.1 / 10.2）.

此包定义与 HTTP 解耦的应用异常层级和错误码注册表。
异常到 HTTP 响应的转换在 API 边界完成
（``app.api.exception_handlers``）。

公开 API:
  - 异常类: ``ApplicationError`` 及其子类
  - ``FieldError``: 字段校验错误项
  - 错误码注册表: ``ErrorCodeRegistry`` 及默认实例 ``default_registry``
"""

from app.core.errors.codes import (
    DuplicateErrorCodeError,
    ErrorCodeMetadata,
    ErrorCodeRegistry,
    InvalidErrorCodeFormatError,
    default_registry,
    register_framework_error_codes,
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

__all__ = [
    "ApplicationError",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "DatabaseConnectionError",
    "DuplicateErrorCodeError",
    "ErrorCodeMetadata",
    "ErrorCodeRegistry",
    "FieldError",
    "InvalidErrorCodeFormatError",
    "NotFoundError",
    "ParameterError",
    "SystemError",
    "UniqueViolationError",
    "ValidationError",
    "default_registry",
    "register_framework_error_codes",
]
