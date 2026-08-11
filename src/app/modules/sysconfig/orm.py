"""系统配置 ORM 模型 — SPEC 8.3 / 16.1 / 16.2.

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 16.1:
  - ``sysconfig_items`` 表存储配置项，``group`` + ``key`` 复合唯一。
  - 敏感配置的 ``stored_value`` 存储加密密文，与加密密钥分离管理（SPEC 23.2）。
  - ``is_core_security`` 标记核心安全配置，普通后台不可覆盖。

ORM 模型只在 Infrastructure 层使用，不泄漏到 Application 或 API 层（SPEC 5.2）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class SysConfigItemORM(Base):
    """系统配置项 ORM 模型 — 映射 ``sysconfig_items`` 表（SPEC 16.1）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``group`` + ``key`` 复合唯一约束（SPEC 16.1: 分组内唯一）。
      - 时间字段使用 ``DateTime(timezone=True)``。

    SPEC 16.1:
      - ``value_type`` 为 string / int / bool / json。
      - ``stored_value`` 存储原始值或加密密文。
      - ``is_sensitive`` 标记敏感配置。
      - ``is_core_security`` 标记核心安全配置。

    SPEC 23.2: 敏感配置的加密密钥不入表，``stored_value`` 只存储密文。
    """

    __tablename__ = "sysconfig_items"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    group: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    stored_value: Mapped[str] = mapped_column(Text, nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_core_security: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
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
            "group",
            "key",
            name="uq_sysconfig_items_group_key",
        ),
        Index("ix_sysconfig_items_group", group),
        Index("ix_sysconfig_items_status", status),
    )
