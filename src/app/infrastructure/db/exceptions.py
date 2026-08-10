"""SQLAlchemy 异常翻译 — SPEC 8.1 / 10.1.

将 SQLAlchemy 底层异常翻译为稳定应用异常，确保 SQLAlchemy 类型
不泄漏到 API 或 Use Case 层（SPEC 8.1: "数据库异常转换为稳定的应用异常"）。

翻译规则:
  - ``IntegrityError``（唯一约束冲突）→ ``UniqueViolationError``
  - ``OperationalError`` / 连接类 ``DBAPIError`` → ``DatabaseConnectionError``
  - 其他 ``SQLAlchemyError`` → ``ApplicationError``（携带原始类型名）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.application.errors import (
    ApplicationError,
    DatabaseConnectionError,
    UniqueViolationError,
)

if TYPE_CHECKING:
    from sqlalchemy.exc import SQLAlchemyError


def translate_db_exception(exc: SQLAlchemyError) -> ApplicationError:
    """将 SQLAlchemy 异常翻译为稳定应用异常.

    SPEC 8.1: "数据库异常转换为稳定的应用异常"。
    SPEC 10.1: "数据库约束错误转换为可理解的应用错误"。

    参数:
        exc: SQLAlchemy 抛出的异常实例。

    返回:
        对应的应用异常，携带稳定错误码。
    """

    from sqlalchemy.exc import (
        DBAPIError,
        IntegrityError,
        OperationalError,
    )

    # 唯一约束冲突：IntegrityError 且底层为 psycopg UniqueViolation
    if isinstance(exc, IntegrityError):
        orig = exc.orig
        if _is_unique_violation(orig):
            return UniqueViolationError(str(exc))
        # 其他完整性约束（外键、检查约束等）暂归为通用应用错误
        return ApplicationError(str(exc))

    # 连接/操作类错误
    if isinstance(exc, OperationalError):
        return DatabaseConnectionError(str(exc))

    # DBAPI 级别连接错误（如 psycopg.OperationalError 被包装为 DBAPIError）
    if isinstance(exc, DBAPIError):
        orig = exc.orig
        if _is_connection_error(orig):
            return DatabaseConnectionError(str(exc))
        return ApplicationError(str(exc))

    # 兜底：其他 SQLAlchemy 异常
    return ApplicationError(f"{type(exc).__name__}: {exc}")


def _is_unique_violation(orig: object) -> bool:
    """判断底层异常是否为 PostgreSQL 唯一约束冲突（SQLSTATE 23505）。"""

    try:
        import psycopg.errors as psycopg_errors

        return isinstance(orig, psycopg_errors.UniqueViolation)
    except ImportError:
        return False


def _is_connection_error(orig: object) -> bool:
    """判断底层异常是否为连接类错误。"""

    try:
        import psycopg.errors as psycopg_errors

        # psycopg 连接类错误的基类
        connection_errors = (
            psycopg_errors.OperationalError,
            psycopg_errors.ConnectionFailure,
            psycopg_errors.CannotConnectNow,
        )
        return isinstance(orig, connection_errors)
    except ImportError:
        return False
