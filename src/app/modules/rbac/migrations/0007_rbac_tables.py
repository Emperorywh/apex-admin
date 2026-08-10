"""RBAC 模块迁移 — 创建 ``rbac_roles``、``rbac_permissions``、
``rbac_role_permissions``、``rbac_user_roles`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 13.1 RBAC 模型:
  - 角色、权限点、角色-权限点关系、用户-角色关系。

SPEC 5.5: 跨模块数据库外键默认禁止（``rbac_user_roles.user_id`` 不做外键）。

Revision ID: 0007_rbac_tables
Revises: 0006_refresh_tokens
Create Date: 2026-08-11 00:00:02
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0007_rbac_tables"
down_revision: str | Sequence[str] | None = "0006_refresh_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 RBAC 表及相关索引和约束。"""

    # ── rbac_permissions 表 — 权限点目录（SPEC 13.1 / 25.2）──
    op.create_table(
        "rbac_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("module_code", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rbac_permissions"),
    )
    op.create_index(
        "ix_rbac_permissions_code_unique",
        "rbac_permissions",
        ["code"],
        unique=True,
    )
    op.create_index(
        "ix_rbac_permissions_module",
        "rbac_permissions",
        ["module_code"],
    )

    # ── rbac_roles 表 — 角色（SPEC 13.1 / 13.2）──
    op.create_table(
        "rbac_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_rbac_roles"),
    )
    op.create_index(
        "ix_rbac_roles_code_unique",
        "rbac_roles",
        ["code"],
        unique=True,
    )
    op.create_index(
        "ix_rbac_roles_status",
        "rbac_roles",
        ["status"],
    )

    # ── rbac_role_permissions 表 — 角色-权限点关联（SPEC 13.1）──
    op.create_table(
        "rbac_role_permissions",
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rbac_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rbac_permissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "role_id",
            "permission_id",
            name="pk_rbac_role_permissions",
        ),
    )
    op.create_index(
        "ix_rbac_role_permissions_permission",
        "rbac_role_permissions",
        ["permission_id"],
    )

    # ── rbac_user_roles 表 — 用户-角色关联（SPEC 13.1 / 13.2）──
    # SPEC 5.5: 跨模块数据库外键默认禁止。user_id 不做外键约束。
    op.create_table(
        "rbac_user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rbac_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint(
            "user_id",
            "role_id",
            name="pk_rbac_user_roles",
        ),
    )
    op.create_index(
        "ix_rbac_user_roles_role",
        "rbac_user_roles",
        ["role_id"],
    )


def downgrade() -> None:
    """删除 RBAC 表及相关索引。"""

    op.drop_index("ix_rbac_user_roles_role", table_name="rbac_user_roles")
    op.drop_table("rbac_user_roles")
    op.drop_index(
        "ix_rbac_role_permissions_permission",
        table_name="rbac_role_permissions",
    )
    op.drop_table("rbac_role_permissions")
    op.drop_index("ix_rbac_roles_status", table_name="rbac_roles")
    op.drop_index("ix_rbac_roles_code_unique", table_name="rbac_roles")
    op.drop_table("rbac_roles")
    op.drop_index("ix_rbac_permissions_module", table_name="rbac_permissions")
    op.drop_index(
        "ix_rbac_permissions_code_unique",
        table_name="rbac_permissions",
    )
    op.drop_table("rbac_permissions")
