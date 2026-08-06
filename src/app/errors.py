"""应用异常基类（SPEC §10.1）。

定义稳定的应用级异常类型，将底层技术异常（数据库、ORM、外部服务）
与业务逻辑解耦。所有对外暴露的异常使用稳定的错误码，不泄露
SQLAlchemy、psycopg 或 SQL 细节。

本模块定义异常类型，具体映射逻辑由 Infrastructure 层实现
（:mod:`app.infrastructure.database.exceptions`）。
"""

from __future__ import annotations


class AppError(Exception):
    """应用异常基类（SPEC §10.1）。

    所有应用级异常的公共基类。每个异常实例携带稳定的错误码，
    客户端业务判断只能使用错误码，不得依赖异常消息文本。

    属性:
        code: 稳定错误码，格式为 ``<MODULE>.<REASON>``（SPEC §10.2）
        detail: 供展示的人类可读说明，不保证稳定，不得用于业务判断
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return f"[{self.code}] {self.detail}"


class IntegrityConstraintError(AppError):
    """数据库完整性约束冲突（SPEC §8.1、§10.1）。

    映射 SQLAlchemy ``IntegrityError``，覆盖唯一约束、外键约束和检查约束
    等违反场景。客户端应据此判断冲突原因并提示用户，不暴露底层约束名。

    属性:
        code: 固定为 ``DB.INTEGRITY_CONSTRAINT``
    """

    def __init__(self, detail: str = "数据库完整性约束冲突") -> None:
        super().__init__(code="DB.INTEGRITY_CONSTRAINT", detail=detail)


class DatabaseOperationError(AppError):
    """数据库操作错误（SPEC §8.1、§10.1）。

    映射 SQLAlchemy ``OperationalError``，覆盖连接失败、超时和死锁等
    操作级错误。此类错误通常需要重试或运维介入。

    属性:
        code: 固定为 ``DB.OPERATION_ERROR``
    """

    def __init__(self, detail: str = "数据库操作错误") -> None:
        super().__init__(code="DB.OPERATION_ERROR", detail=detail)
