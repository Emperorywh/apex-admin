"""示例模块迁移 — 创建 ``example_items`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - ``name`` 具有唯一约束。
  - 时间字段使用 ``timestamptz``。
  - 为 ``created_at`` 创建索引，支持按时间排序查询。

此表属于示例模块。派生项目删除示例模块时应同时删除此迁移记录
并重置 Alembic head（SPEC 8.2: 不提供旧系统数据兼容迁移）。

Revision ID: 0002_example_items
Revises: 0001_initial
Create Date: 2026-08-10 00:00:00
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0002_example_items"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 ``example_items`` 表及相关索引和约束."""

    op.create_table(
        "example_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_example_items"),
        sa.UniqueConstraint("name", name="uq_example_items_name"),
    )
    op.create_index(
        "ix_example_items_created_at",
        "example_items",
        ["created_at"],
    )


def downgrade() -> None:
    """删除 ``example_items`` 表及相关索引和约束."""

    op.drop_index("ix_example_items_created_at", table_name="example_items")
    op.drop_table("example_items")
