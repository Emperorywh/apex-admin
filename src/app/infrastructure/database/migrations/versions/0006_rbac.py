"""RBAC 模块迁移：创建 roles、user_roles、role_permissions 表

创建 RBAC 模块的数据表（SPEC §13.1）：

- ``roles``：角色表，含编码、名称、状态、描述、内置标志和超级管理员标志
- ``user_roles``：用户-角色关系表（多对多）
- ``role_permissions``：角色-权限关系表（多对多，权限以编码引用）

权限点通过 ModuleDefinition 声明和注册（SPEC §5.5、§13.1），
不创建单独的权限表——role_permissions 以稳定编码引用权限点。

``down_revision`` 指向 ``0005``（login_security 表），确保单头约束
（SPEC §5.5、§8.2）。

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08 00:00:01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 roles、user_roles、role_permissions 表。"""
    # -- roles 表（SPEC §13.1、§13.2、§13.4）--
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "is_super_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    )
    # 角色编码唯一索引（SPEC §13.1：角色编码全局唯一）
    op.create_index(
        "ix_roles_code",
        "roles",
        ["code"],
        unique=True,
    )

    # -- user_roles 表（SPEC §13.1：用户与角色关系）--
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "role_id",
            sa.Uuid(),
            sa.ForeignKey("roles.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
    )
    # 按角色查询成员索引
    op.create_index(
        "ix_user_roles_role_id",
        "user_roles",
        ["role_id"],
    )

    # -- role_permissions 表（SPEC §13.1：角色与权限点关系）--
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id",
            sa.Uuid(),
            sa.ForeignKey("roles.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "permission_code",
            sa.String(length=100),
            primary_key=True,
            nullable=False,
        ),
    )
    # 按权限编码查询索引（用于反向查询哪些角色拥有某权限）
    op.create_index(
        "ix_role_permissions_permission_code",
        "role_permissions",
        ["permission_code"],
    )


def downgrade() -> None:
    """删除 roles、user_roles、role_permissions 表。"""
    op.drop_index("ix_role_permissions_permission_code", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_index("ix_roles_code", table_name="roles")
    op.drop_table("roles")
