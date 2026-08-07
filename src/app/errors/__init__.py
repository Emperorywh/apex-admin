"""应用异常体系（SPEC §10.1、§10.2）。

公共 API：
    - 异常类型：``AppError`` 及六种基类
    - 数据库专用异常：``IntegrityConstraintError``、``DatabaseOperationError``
    - 字段校验错误项：``FieldError``
    - 错误码工具：``is_valid_error_code``、``is_framework_code``、``build_problem_type``
"""

from __future__ import annotations

from app.errors.base import (
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
)
from app.errors.codes import (
    FRAMEWORK_MODULE_PREFIXES,
    build_problem_type,
    is_framework_code,
    is_valid_error_code,
)

__all__ = [
    "AppError",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "DatabaseOperationError",
    "FieldError",
    "FRAMEWORK_MODULE_PREFIXES",
    "IntegrityConstraintError",
    "NotFoundError",
    "ParameterError",
    "SystemError",
    "build_problem_type",
    "is_framework_code",
    "is_valid_error_code",
]
