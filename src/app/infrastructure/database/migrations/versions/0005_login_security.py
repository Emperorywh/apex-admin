"""登录安全迁移：创建 login_attempts 表

创建暴力破解防护的登录失败记录表（SPEC §12.4）：

- ``login_attempts``：以维度（account / ip）和标识符为复合主键，
  统计连续失败次数和限制状态。持久化到 PostgreSQL 以跨多 Worker 工作
  （SPEC §12.4）。

``down_revision`` 指向 ``0004``（auth 表），确保单头约束
（SPEC §5.5、§8.2）。

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07 00:00:04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 login_attempts 表。"""
    op.create_table(
        "login_attempts",
        sa.Column("dimension", sa.String(length=10), nullable=False),
        sa.Column("identifier", sa.String(length=255), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_failure_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("dimension", "identifier"),
    )
    # 按标识符查询索引（SPEC §12.4：账号 / IP 维度查询）
    op.create_index(
        "ix_login_attempts_identifier",
        "login_attempts",
        ["identifier"],
    )


def downgrade() -> None:
    """删除 login_attempts 表。"""
    op.drop_index("ix_login_attempts_identifier", table_name="login_attempts")
    op.drop_table("login_attempts")
