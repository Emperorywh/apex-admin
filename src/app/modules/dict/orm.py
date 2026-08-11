"""数据字典 ORM 模型 — SPEC 8.3 / 17.1 / 17.2.

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 17.1:
  - ``dict_types`` 表存储字典类型，``code`` 全局唯一。
  - ``dict_references`` 表存储引用登记记录，``dict_type_code`` + ``module_code``
    + ``resource_id`` 复合唯一（防重复登记）。

SPEC 17.2:
  - ``dict_items`` 表存储字典项，``label`` 为显示文本，``value`` 为稳定值。
  - 业务数据持久化 ``value``（稳定值），而非 ``label``（展示文本）。

ORM 模型只在 Infrastructure 层使用，不泄漏到 Application 或 API 层（SPEC 5.2）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class DictTypeORM(Base):
    """字典类型 ORM 模型 — 映射 ``dict_types`` 表（SPEC 17.1）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``code`` 全局唯一约束（SPEC 17.1: 字典编码保持稳定和唯一）。
      - 时间字段使用 ``DateTime(timezone=True)``。

    SPEC 17.1:
      - ``code`` 为稳定编码，不随显示名变更。
      - ``status`` 标记启用/禁用状态。
    """

    __tablename__ = "dict_types"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
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
        UniqueConstraint("code", name="uq_dict_types_code"),
        Index("ix_dict_types_status", status),
    )


class DictItemORM(Base):
    """字典项 ORM 模型 — 映射 ``dict_items`` 表（SPEC 17.2）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``dict_type_id`` 外键引用 ``dict_types.id``。
      - ``dict_type_id`` + ``value`` 复合唯一（同一字典类型内稳定值唯一）。

    SPEC 17.2:
      - ``label`` 为显示文本（人类可读，可随 UI 需求变更）。
      - ``value`` 为稳定值（业务持久化此值，不变更）。
      - ``sort_order`` 排序序号。
      - ``metadata_`` 扩展元数据（JSONB）。
    """

    __tablename__ = "dict_items"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dict_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("dict_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
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
        UniqueConstraint(
            "dict_type_id",
            "value",
            name="uq_dict_items_type_value",
        ),
        Index("ix_dict_items_dict_type_id", dict_type_id),
        Index("ix_dict_items_status", status),
    )


class DictReferenceORM(Base):
    """字典引用登记 ORM 模型 — 映射 ``dict_references`` 表.

    SPEC 17.1: "已被业务引用的字典类型具有删除保护"。
    基座提供引用登记 Port，业务模块通过 Port 登记引用。
    删除字典类型时检查是否存在引用登记。

    ``dict_type_code`` + ``module_code`` + ``resource_id`` 复合唯一约束
    防止重复登记。
    """

    __tablename__ = "dict_references"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dict_type_code: Mapped[str] = mapped_column(String(100), nullable=False)
    module_code: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "dict_type_code",
            "module_code",
            "resource_id",
            name="uq_dict_references_type_module_resource",
        ),
        Index(
            "ix_dict_references_dict_type_code",
            dict_type_code,
        ),
    )
