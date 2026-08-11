"""组织模块迁移 — 创建 ``org_departments`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 14.1 部门管理:
  - 部门为树形实体，通过 ``parent_id`` 自引用实现父子层级。
  - ``leader_id`` 引用用户 ID（跨模块不建数据库外键，SPEC 5.5）。
  - ``parent_id`` 使用 ``ondelete=RESTRICT`` 防止删除有子部门的部门时
    数据库层面的孤儿记录（应用层已在 Use Case 中拒绝，此处为终极防护）。

SPEC 5.5: ``leader_id`` 不做外键约束。

Revision ID: 0008_org_departments
Revises: 0007_rbac_tables
Create Date: 2026-08-11 00:00:03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0008_org_departments"
down_revision: str | Sequence[str] | None = "0007_rbac_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 org_departments 表及相关索引。"""

    op.create_table(
        "org_departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org_departments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        # SPEC 5.5: leader_id 跨模块引用用户 ID，不做外键约束。
        sa.Column("leader_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_org_departments"),
    )
    op.create_index(
        "ix_org_departments_code_unique",
        "org_departments",
        ["code"],
        unique=True,
    )
    op.create_index(
        "ix_org_departments_parent",
        "org_departments",
        ["parent_id"],
    )
    op.create_index(
        "ix_org_departments_status",
        "org_departments",
        ["status"],
    )


def downgrade() -> None:
    """删除 org_departments 表及相关索引。"""

    op.drop_index("ix_org_departments_status", table_name="org_departments")
    op.drop_index("ix_org_departments_parent", table_name="org_departments")
    op.drop_index(
        "ix_org_departments_code_unique",
        table_name="org_departments",
    )
    op.drop_table("org_departments")
