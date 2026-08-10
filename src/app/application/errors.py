"""应用层稳定异常 — SPEC 8.1 / 10.1 / 10.2.

数据库基础设施层捕获 SQLAlchemy 异常后翻译为这些稳定应用异常，
确保 SQLAlchemy 类型不泄漏到 API 或 Use Case 层（SPEC 8.1）。

每个异常携带全局唯一的稳定错误码（SPEC 10.2），客户端可据此
执行业务判断，不依赖可变的展示文案。
"""

from __future__ import annotations


class ApplicationError(Exception):
    """应用层基础异常.

    所有应用层异常的基类。携带稳定错误码 ``code``，
    客户端以此做业务判断（SPEC 10.2）。
    """

    code: str = "APPLICATION.ERROR"


class UniqueViolationError(ApplicationError):
    """唯一约束冲突异常.

    当 INSERT 或 UPDATE 违反数据库唯一约束时抛出。
    SPEC 8.3: "唯一性规则优先由数据库唯一约束保证"。
    SPEC 8.4: "冲突错误具有明确的业务错误码"。
    """

    code = "DB.UNIQUE_VIOLATION"


class DatabaseConnectionError(ApplicationError):
    """数据库连接异常.

    当无法连接数据库或连接中断时抛出。
    SPEC 6.1: "数据库暂时不可用只影响就绪状态"。
    """

    code = "DB.CONNECTION_ERROR"
