"""数据字典模块迁移 — 创建 ``dict_types`` / ``dict_items`` / ``dict_references`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 17.1:
  - ``dict_types`` 表存储字典类型，``code`` 全局唯一。
  - ``dict_references`` 表存储引用登记记录。

SPEC 17.2:
  - ``dict_items`` 表存储字典项，``label`` 为显示文本，``value`` 为稳定值。
  - ``dict_type_id`` + ``value`` 复合唯一约束。

Revision ID: 0012_dict_tables
Revises: 0011_sysconfig_tables
Create Date: 2026-08-11 00:00:07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0012_dict_tables"
down_revision: str | Sequence[str] | None = "0011_sysconfig_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建字典类型、字典项和引用登记表及相关索引。"""

    # 字典类型表
    op.create_table(
        "dict_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_dict_types_code"),
    )

    op.create_index(
        "ix_dict_types_status",
        "dict_types",
        ["status"],
    )

    # 字典项表
    op.create_table(
        "dict_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "dict_type_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("metadata_", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(
            ["dict_type_id"],
            ["dict_types.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dict_type_id",
            "value",
            name="uq_dict_items_type_value",
        ),
    )

    op.create_index(
        "ix_dict_items_dict_type_id",
        "dict_items",
        ["dict_type_id"],
    )
    op.create_index(
        "ix_dict_items_status",
        "dict_items",
        ["status"],
    )

    # 引用登记表 — SPEC 17.1: 删除保护
    op.create_table(
        "dict_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dict_type_code", sa.String(100), nullable=False),
        sa.Column("module_code", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dict_type_code",
            "module_code",
            "resource_id",
            name="uq_dict_references_type_module_resource",
        ),
    )

    op.create_index(
        "ix_dict_references_dict_type_code",
        "dict_references",
        ["dict_type_code"],
    )


def downgrade() -> None:
    """删除字典相关表.

    不可逆——字典数据需要手动备份恢复。
    """

    op.drop_index(
        "ix_dict_references_dict_type_code",
        table_name="dict_references",
    )
    op.drop_table("dict_references")

    op.drop_index("ix_dict_items_status", table_name="dict_items")
    op.drop_index("ix_dict_items_dict_type_id", table_name="dict_items")
    op.drop_table("dict_items")

    op.drop_index("ix_dict_types_status", table_name="dict_types")
    op.drop_table("dict_types")
