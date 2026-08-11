"""组织模块迁移 — 创建岗位与用户组织关系表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 14.2 岗位管理:
  - ``org_posts`` 表存储岗位数据。
  - ``org_user_posts`` 表存储用户-岗位关系，唯一约束 ``(user_id, post_id)``
    保证分配幂等且防重复。

SPEC 14.3 用户组织关系:
  - ``org_user_departments`` 表存储用户-部门关系。
  - 基座默认仅主部门——唯一约束 ``(user_id)`` 保证一个用户仅一个主部门。
  - SPEC 5.5: ``user_id`` 跨模块引用 identity 模块用户 ID，不做外键约束。

SPEC 5.5: ``user_id`` 不做外键约束。

Revision ID: 0009_org_posts
Revises: 0008_org_departments
Create Date: 2026-08-11 00:00:04
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0009_org_posts"
down_revision: str | Sequence[str] | None = "0008_org_departments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 org_posts、org_user_departments、org_user_posts 表及相关索引。"""

    # ── 岗位表 ───────────────────────────────────────────────────────────
    op.create_table(
        "org_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_org_posts"),
    )
    op.create_index(
        "ix_org_posts_code_unique",
        "org_posts",
        ["code"],
        unique=True,
    )
    op.create_index(
        "ix_org_posts_status",
        "org_posts",
        ["status"],
    )

    # ── 用户-部门关系表 ─────────────────────────────────────────────────
    op.create_table(
        "org_user_departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # SPEC 5.5: user_id 跨模块引用 identity 模块用户 ID，不做外键约束。
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org_departments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_org_user_departments"),
    )
    # SPEC 14.3: 基座默认仅主部门——唯一约束保证一个用户仅一个主部门。
    op.create_index(
        "ix_org_user_depts_user_unique",
        "org_user_departments",
        ["user_id"],
        unique=True,
    )
    op.create_index(
        "ix_org_user_depts_dept",
        "org_user_departments",
        ["department_id"],
    )

    # ── 用户-岗位关系表 ─────────────────────────────────────────────────
    op.create_table(
        "org_user_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # SPEC 5.5: user_id 跨模块引用 identity 模块用户 ID，不做外键约束。
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org_posts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_org_user_posts"),
    )
    # SPEC 14.2: 唯一约束 (user_id, post_id) 保证分配幂等且防重复。
    op.create_index(
        "ix_org_user_posts_unique",
        "org_user_posts",
        ["user_id", "post_id"],
        unique=True,
    )
    op.create_index(
        "ix_org_user_posts_user",
        "org_user_posts",
        ["user_id"],
    )
    op.create_index(
        "ix_org_user_posts_post",
        "org_user_posts",
        ["post_id"],
    )


def downgrade() -> None:
    """删除岗位与用户组织关系表及相关索引。"""

    op.drop_index("ix_org_user_posts_post", table_name="org_user_posts")
    op.drop_index("ix_org_user_posts_user", table_name="org_user_posts")
    op.drop_index("ix_org_user_posts_unique", table_name="org_user_posts")
    op.drop_table("org_user_posts")

    op.drop_index("ix_org_user_depts_dept", table_name="org_user_departments")
    op.drop_index("ix_org_user_depts_user_unique", table_name="org_user_departments")
    op.drop_table("org_user_departments")

    op.drop_index("ix_org_posts_status", table_name="org_posts")
    op.drop_index("ix_org_posts_code_unique", table_name="org_posts")
    op.drop_table("org_posts")
