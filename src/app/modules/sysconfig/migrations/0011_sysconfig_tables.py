"""系统配置模块迁移 — 创建 ``sysconfig_items`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 16.1:
  - ``sysconfig_items`` 表存储配置项。
  - ``group`` + ``key`` 复合唯一约束（SPEC 16.1: 分组内唯一）。
  - ``stored_value`` 存储原始值或加密密文（SPEC 23.2: 密钥与密文分离）。
  - ``is_sensitive`` 标记敏感配置（加密存储且不回显）。
  - ``is_core_security`` 标记核心安全配置（普通后台不可覆盖）。

Revision ID: 0011_sysconfig_tables
Revises: 0010_menu_tables
Create Date: 2026-08-11 00:00:06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0011_sysconfig_tables"
down_revision: str | Sequence[str] | None = "0010_menu_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 sysconfig_items 表及相关索引。"""

    op.create_table(
        "sysconfig_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group", sa.String(100), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value_type", sa.String(20), nullable=False),
        sa.Column("stored_value", sa.Text(), nullable=False),
        sa.Column(
            "is_sensitive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_core_security",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
        sa.UniqueConstraint(
            "group",
            "key",
            name="uq_sysconfig_items_group_key",
        ),
    )

    op.create_index(
        "ix_sysconfig_items_group",
        "sysconfig_items",
        ["group"],
    )
    op.create_index(
        "ix_sysconfig_items_status",
        "sysconfig_items",
        ["status"],
    )


def downgrade() -> None:
    """删除 sysconfig_items 表.

    不可逆——配置数据需要手动备份恢复。
    """

    op.drop_index("ix_sysconfig_items_status", table_name="sysconfig_items")
    op.drop_index("ix_sysconfig_items_group", table_name="sysconfig_items")
    op.drop_table("sysconfig_items")
