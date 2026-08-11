"""组织模块 ORM 模型 — SPEC 8.3 / 14.1.

SPEC 8.3 数据建模规范:
  - 每张业务表具有明确主键。
  - 表名、字段名、索引名遵循统一规范。
  - 唯一性规则优先由数据库唯一约束保证。
  - 时间字段使用 ``timestamptz``，统一 UTC（SPEC 6.3）。

SPEC 14.1 部门管理:
  - 部门为树形实体，通过 ``parent_id`` 自引用实现父子层级。
  - ``leader_id`` 引用用户 ID（跨模块不建数据库外键，SPEC 5.5）。

SPEC 5.5: ``leader_id`` 不做外键约束，引用 identity 模块的 ``users`` 表，
通过应用层 Port 校验用户存在性。``parent_id`` 为同模块自引用外键。

ORM 模型继承自全局 ``Base``，Alembic 通过 ``Base.metadata`` 收集表结构
（SPEC 8.2）。ORM 模型只在 Infrastructure 层使用，不泄漏到 Application
或 API 层（SPEC 5.2）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class DepartmentORM(Base):
    """部门 ORM 模型 — 映射 ``org_departments`` 表（SPEC 14.1）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``code`` 具有唯一约束，保证部门编码全局唯一。
      - ``parent_id`` 自引用外键（同模块），实现树形层级。
      - 时间字段使用 ``DateTime(timezone=True)``（PostgreSQL ``timestamptz``）。

    SPEC 5.5: ``leader_id`` 跨模块引用用户 ID，不做数据库外键约束。
    """

    __tablename__ = "org_departments"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("org_departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leader_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("ix_org_departments_code_unique", code, unique=True),
        Index("ix_org_departments_parent", parent_id),
        Index("ix_org_departments_status", status),
    )
