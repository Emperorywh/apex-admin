"""ORM 声明基类 — SQLAlchemy 2.0 Declarative.

所有业务模块的 ORM 模型继承自此 ``Base``。
Alembic env.py 通过 ``Base.metadata`` 作为 ``target_metadata``
支持 autogenerate（SPEC 8.2）。

G1 阶段无业务模块表结构，metadata 为空。
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """全局 ORM 声明基类.

    所有模块的 ORM 模型继承此类。Alembic 通过 ``Base.metadata``
    收集表结构用于迁移生成与比较。
    """
