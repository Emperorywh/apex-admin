"""应用层异常门面 — 异常层级定义已迁移至 ``app.core.errors``.

异常层级定义移至 ``app.core.errors.exceptions`` 以支持跨层共享
（Infrastructure 翻译数据库异常、API 边界统一转换）。
此模块保留应用层导入入口，维持现有导入路径。

SPEC 10.1 / 10.2 的完整错误处理体系见 ``app.core.errors`` 包。
"""

from __future__ import annotations

from app.core.errors.exceptions import (
    ApplicationError,
    DatabaseConnectionError,
    UniqueViolationError,
)

__all__ = [
    "ApplicationError",
    "DatabaseConnectionError",
    "UniqueViolationError",
]
