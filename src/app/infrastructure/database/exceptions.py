"""数据库异常 → 应用异常映射（SPEC §8.1、§10.1）。

将 SQLAlchemy 底层异常转换为稳定的、与技术无关的应用异常。
映射后的异常属于 :mod:`app.errors`，不泄露 SQLAlchemy、psycopg 或 SQL 细节。

映射规则：
- ``IntegrityError``（唯一约束、外键、检查约束冲突）→ :class:`~app.errors.IntegrityConstraintError`
- ``OperationalError``（连接失败、超时、死锁）→ :class:`~app.errors.DatabaseOperationError`
- 其他异常保持原样传播

Repository 适配器和 UoW 在执行数据库操作后调用 :func:`translate_db_exception`
统一映射异常，确保 Application 层只感知稳定的应用异常。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError, OperationalError

from app.errors import DatabaseOperationError, IntegrityConstraintError


def translate_db_exception(exc: Exception) -> Exception:
    """将 SQLAlchemy 异常映射为稳定应用异常（SPEC §8.1、§10.1）。

    根据 SQLAlchemy 异常类型转换为对应的应用异常。若异常类型不在
    映射范围内，则原样返回，由上层决定处理方式。

    Args:
        exc: 数据库操作中捕获的异常

    Returns:
        映射后的应用异常（:class:`~app.errors.IntegrityConstraintError` 或
        :class:`~app.errors.DatabaseOperationError`），或原始异常（不匹配时）

    使用方式::

        try:
            await session.commit()
        except Exception as exc:
            raise translate_db_exception(exc) from exc
    """
    if isinstance(exc, IntegrityError):
        return IntegrityConstraintError(
            detail="数据库完整性约束冲突：唯一约束、外键或检查约束被违反"
        )
    if isinstance(exc, OperationalError):
        return DatabaseOperationError(detail="数据库操作错误：连接失败、超时或死锁")
    return exc
