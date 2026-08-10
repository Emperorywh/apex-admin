"""示例 ORM 模型 — SPEC 8.3.

SPEC 8.3 数据建模规范:
  - 每张业务表具有明确主键。
  - 表名、字段名遵循统一规范。
  - 唯一性规则优先由数据库唯一约束保证。
  - 时间字段使用 ``timestamptz``，统一 UTC（SPEC 6.3）。

ORM 模型继承自全局 ``Base``，Alembic 通过 ``Base.metadata`` 收集表结构
（SPEC 8.2）。ORM 模型只在 Infrastructure 层使用，不泄漏到 Application 或 API 层
（SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class ExampleItemORM(Base):
    """示例条目 ORM 模型 — 映射 ``example_items`` 表.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``name`` 具有唯一约束，保证名称全局唯一。
      - 时间字段使用 ``DateTime(timezone=True)``（PostgreSQL ``timestamptz``）。
    """

    __tablename__ = "example_items"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
