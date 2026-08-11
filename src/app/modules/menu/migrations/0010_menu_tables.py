"""菜单模块迁移 — 创建 ``menu_menus`` 和 ``menu_role_menus`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 15.1 菜单资源:
  - ``menu_menus`` 表存储菜单数据，``parent_id`` 自引用实现树形层级。
  - ``parent_id`` 使用 ``ondelete=RESTRICT`` 防止孤儿记录。
  - ``menu_type`` 区分目录/页面/外链。

SPEC 15.2 角色菜单:
  - ``menu_role_menus`` 关联表，复合主键 (role_id, menu_id)。
  - SPEC 5.5: ``role_id`` 跨模块引用 RBAC 角色 ID，不做外键约束。

Revision ID: 0010_menu_tables
Revises: 0009_org_posts
Create Date: 2026-08-11 00:00:05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0010_menu_tables"
down_revision: str | Sequence[str] | None = "0009_org_posts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 menu_menus 和 menu_role_menus 表及相关索引。"""

    op.create_table(
        "menu_menus",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("menu_menus.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("menu_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("path", sa.String(500), nullable=True),
        sa.Column("component", sa.String(500), nullable=True),
        sa.Column("icon", sa.String(200), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("visible", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_menu_menus"),
    )
    op.create_index(
        "ix_menu_menus_parent",
        "menu_menus",
        ["parent_id"],
    )
    op.create_index(
        "ix_menu_menus_status",
        "menu_menus",
        ["status"],
    )

    op.create_table(
        "menu_role_menus",
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "menu_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("menu_menus.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "menu_id", name="pk_menu_role_menus"),
    )
    op.create_index(
        "ix_menu_role_menus_menu",
        "menu_role_menus",
        ["menu_id"],
    )


def downgrade() -> None:
    """删除 menu_role_menus 和 menu_menus 表及相关索引。"""

    op.drop_index("ix_menu_role_menus_menu", table_name="menu_role_menus")
    op.drop_table("menu_role_menus")
    op.drop_index("ix_menu_menus_status", table_name="menu_menus")
    op.drop_index("ix_menu_menus_parent", table_name="menu_menus")
    op.drop_table("menu_menus")
