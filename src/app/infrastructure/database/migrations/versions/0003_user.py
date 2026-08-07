"""用户模块迁移：创建 users 表

创建用户管理模块的数据表 ``users``（SPEC §11.2）。

表包含用户名、显示名称、密码哈希、状态、手机号、邮箱、
最近登录时间、密码更新时间、创建/更新时间、创建/更新人字段。
``username`` 具有唯一约束保证用户名全局唯一（SPEC §11.2）。
状态使用 ``String`` 列存储稳定编码（SPEC §8.3）。

``down_revision`` 指向生成时的全局 head（``0002``），确保单头约束
（SPEC §5.5、§8.2）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07 00:00:02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 users 表。"""
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "password_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
    )
    # 用户名唯一索引——保证用户名全局唯一（SPEC §11.2、§8.3）
    op.create_index(
        "ix_users_username",
        "users",
        ["username"],
        unique=True,
    )


def downgrade() -> None:
    """删除 users 表。"""
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
