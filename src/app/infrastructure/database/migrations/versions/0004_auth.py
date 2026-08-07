"""认证模块迁移：创建 sessions、access_tokens、refresh_tokens 表

创建认证与会话模块的数据表（SPEC §12.2、§12.3）：

- ``sessions``：服务端会话，记录设备、IP、User-Agent、创建时间、
  最近活动时间和超时配置（SPEC §12.3）
- ``access_tokens``：Access Token HMAC-SHA-256 摘要（SPEC §12.2：
  只保存摘要，不保存明文 Token）
- ``refresh_tokens``：Refresh Token HMAC-SHA-256 摘要（独立密钥），
  记录 Token Family、前驱和时间信息（SPEC §12.2）

``down_revision`` 指向 ``0003``（users 表），确保单头约束
（SPEC §5.5、§8.2）。

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-07 00:00:03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 sessions、access_tokens、refresh_tokens 表。"""
    # -- sessions 表（SPEC §12.3）--
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("device", sa.String(length=255), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("absolute_timeout_hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("revoked_reason", sa.String(length=100), nullable=True),
    )
    # 按用户查询会话索引（SPEC §12.3：查看活动会话）
    op.create_index(
        "ix_sessions_user_id",
        "sessions",
        ["user_id"],
    )

    # -- access_tokens 表（SPEC §12.2）--
    op.create_table(
        "access_tokens",
        sa.Column("digest", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    # 按会话查询/删除 Access Token 索引
    op.create_index(
        "ix_access_tokens_session_id",
        "access_tokens",
        ["session_id"],
    )

    # -- refresh_tokens 表（SPEC §12.2）--
    op.create_table(
        "refresh_tokens",
        sa.Column("digest", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("token_family_id", sa.Uuid(), nullable=False),
        sa.Column(
            "predecessor_digest",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("revoked_reason", sa.String(length=100), nullable=True),
    )
    # 按会话和 Token Family 查询索引（TASK-016 轮换/重放检测使用）
    op.create_index(
        "ix_refresh_tokens_session_id",
        "refresh_tokens",
        ["session_id"],
    )
    op.create_index(
        "ix_refresh_tokens_token_family_id",
        "refresh_tokens",
        ["token_family_id"],
    )


def downgrade() -> None:
    """删除 sessions、access_tokens、refresh_tokens 表。"""
    op.drop_index("ix_refresh_tokens_token_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_session_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_access_tokens_session_id", table_name="access_tokens")
    op.drop_table("access_tokens")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
