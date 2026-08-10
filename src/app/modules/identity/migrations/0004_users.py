"""用户模块迁移 — 创建 ``users`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。
  - 敏感字段明确哈希策略。

SPEC 11.2 用户字段:
  - 用户名、显示名、密码哈希、状态、手机号/邮箱。
  - 最近登录时间、密码更新时间。
  - 创建/更新时间与操作人。

SPEC 23.2: 密码哈希使用 Argon2id，不存储明文。

Revision ID: 0004_users
Revises: 0003_audit_tables
Create Date: 2026-08-11 00:00:00
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0004_users"
down_revision: str | Sequence[str] | None = "0003_audit_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建用户表及相关索引和约束。"""

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        # password_hash 存储 Argon2id PHC 格式哈希字符串（~100 字符），
        # 预留 255 字符以兼容未来参数调整。
        sa.Column("password_hash", sa.String(255), nullable=False),
        # 状态以稳定字符串编码存储（SPEC 8.3），不使用数据库枚举类型。
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "password_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )

    # 用户名唯一约束 — SPEC 8.3: 唯一性规则优先由数据库唯一约束保证。
    # 冲突时由数据库拦截，翻译为稳定冲突错误码 USER.ALREADY_EXISTS（SPEC 8.4）。
    op.create_index(
        "ix_users_username_unique",
        "users",
        ["username"],
        unique=True,
    )

    # 状态索引 — 支持按状态筛选分页查询。
    op.create_index(
        "ix_users_status",
        "users",
        ["status"],
    )

    # 创建时间索引 — 支持按创建时间排序分页。
    op.create_index(
        "ix_users_created_at",
        "users",
        ["created_at"],
    )


def downgrade() -> None:
    """删除用户表及相关索引。"""

    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_username_unique", table_name="users")
    op.drop_table("users")
