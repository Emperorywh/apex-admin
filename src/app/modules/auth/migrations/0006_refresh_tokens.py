"""认证模块迁移 — 创建 ``auth_refresh_tokens`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 12.2:
  - 数据库只保存 Refresh Token 的 HMAC-SHA-256 摘要，不保存明文。
  - 每个 Refresh Token 记录所属 Session、Token Family、前驱、创建时间、
    使用时间、过期时间和吊销原因。
  - 刷新事务对 Token Family 加行锁。

SPEC 5.5: 跨模块数据库外键默认禁止（``session_id`` 不做外键）。

Revision ID: 0006_refresh_tokens
Revises: 0005_auth_tables
Create Date: 2026-08-11 00:00:01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0006_refresh_tokens"
down_revision: str | Sequence[str] | None = "0005_auth_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Refresh Token 表及相关索引。"""

    # ── auth_refresh_tokens 表 — SPEC 12.2 ──
    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        # token_digest: HMAC-SHA-256 十六进制摘要（64 字符）
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("predecessor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_reason", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_refresh_tokens"),
    )

    # Token 摘要唯一索引 — SPEC 12.2: 每个摘要最多一条记录。
    op.create_index(
        "ix_auth_refresh_tokens_digest_unique",
        "auth_refresh_tokens",
        ["token_digest"],
        unique=True,
    )

    # 会话 ID 索引 — 支持按会话查询和批量吊销。
    op.create_index(
        "ix_auth_refresh_tokens_session_id",
        "auth_refresh_tokens",
        ["session_id"],
    )

    # Token Family 索引 — 刷新事务对 Family 加行锁。
    op.create_index(
        "ix_auth_refresh_tokens_family_id",
        "auth_refresh_tokens",
        ["family_id"],
    )


def downgrade() -> None:
    """删除 Refresh Token 表及相关索引。"""

    op.drop_index(
        "ix_auth_refresh_tokens_family_id",
        table_name="auth_refresh_tokens",
    )
    op.drop_index(
        "ix_auth_refresh_tokens_session_id",
        table_name="auth_refresh_tokens",
    )
    op.drop_index(
        "ix_auth_refresh_tokens_digest_unique",
        table_name="auth_refresh_tokens",
    )
    op.drop_table("auth_refresh_tokens")
