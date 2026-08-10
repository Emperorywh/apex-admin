"""认证模块迁移 — 创建 ``auth_sessions`` 和 ``auth_login_attempts`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 12.2: 数据库只保存 Token 的 HMAC-SHA-256 摘要，不保存明文。
SPEC 12.3: 会话信息持久化到 PostgreSQL。
SPEC 12.4: 登录失败状态持久化到 PostgreSQL。
SPEC 5.5: 跨模块数据库外键默认禁止（``user_id`` 不做外键）。

Revision ID: 0005_auth_tables
Revises: 0004_users
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
revision: str = "0005_auth_tables"
down_revision: str | Sequence[str] | None = "0004_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建认证模块表及相关索引和约束。"""

    # ── auth_sessions 表 — SPEC 12.3 ──
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # access_token_digest: HMAC-SHA-256 十六进制摘要（64 字符）
        sa.Column("access_token_digest", sa.String(64), nullable=False),
        sa.Column("device", sa.String(200), nullable=True),
        sa.Column("ip_address", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("revoked_reason", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
    )

    # Access Token 摘要唯一索引 — SPEC 12.2
    op.create_index(
        "ix_auth_sessions_token_digest_unique",
        "auth_sessions",
        ["access_token_digest"],
        unique=True,
    )

    # 用户 ID 索引 — 支持按用户查询活动会话和批量吊销
    op.create_index(
        "ix_auth_sessions_user_id",
        "auth_sessions",
        ["user_id"],
    )

    # ── auth_login_attempts 表 — SPEC 12.4 ──
    op.create_table(
        "auth_login_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_login_attempts"),
    )

    # 维度+键唯一约束 — 每个维度键只有一条计数记录
    op.create_index(
        "ix_auth_login_attempts_dimension_key_unique",
        "auth_login_attempts",
        ["dimension", "key"],
        unique=True,
    )


def downgrade() -> None:
    """删除认证模块表及相关索引。"""

    op.drop_index(
        "ix_auth_login_attempts_dimension_key_unique",
        table_name="auth_login_attempts",
    )
    op.drop_table("auth_login_attempts")

    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_index(
        "ix_auth_sessions_token_digest_unique",
        table_name="auth_sessions",
    )
    op.drop_table("auth_sessions")
