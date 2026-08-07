"""示例模块迁移：创建 example_items 表

创建最小示例模块的数据表 ``example_items``，验证模块迁移与全局
Alembic revision 图的正确链接（SPEC §5.5、§8.2）。

``down_revision`` 指向生成时的全局 head（``0001``），确保单头约束。

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07 00:00:01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 example_items 表。"""
    op.create_table(
        "example_items",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """删除 example_items 表。"""
    op.drop_table("example_items")
