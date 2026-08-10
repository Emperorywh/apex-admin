"""审计模块迁移 — 创建 ``audit_logs`` 和 ``login_logs`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 审计日志等不可变数据不得通过通用 CRUD 随意修改。

SPEC 18.1 / 18.2:
  - 审计日志记录操作者/目标显示名快照、模块、动作、差异等。
  - 登录日志记录用户/会话/IP/UA/时间/结果。
  - 两张表均不含密码和 Token 列。

Revision ID: 0003_audit_tables
Revises: 0002_example_items
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
revision: str = "0003_audit_tables"
down_revision: str | Sequence[str] | None = "0002_example_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建审计日志和登录日志表及相关索引。"""

    # ── 操作审计表（SPEC 18.2）──
    # 显示名快照字段（actor_display_name、resource_display_name）在操作
    # 发生时写入，后续源数据变更不影响历史审计记录。
    # diff 列为 JSONB，存储字段白名单生成的变更差异。
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(100), nullable=True),
        sa.Column("actor_display_name", sa.String(200), nullable=False),
        sa.Column("module", sa.String(100), nullable=False),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=True),
        sa.Column("resource_display_name", sa.String(200), nullable=True),
        sa.Column("result", sa.String(50), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )

    # 索引 — 支持 G3 审计查询（SPEC 18.3: 按操作者、模块、动作、资源、
    # 结果和时间范围筛选）。
    op.create_index(
        "ix_audit_logs_occurred_at",
        "audit_logs",
        ["occurred_at"],
    )
    op.create_index(
        "ix_audit_logs_actor_id",
        "audit_logs",
        ["actor_id"],
    )
    op.create_index(
        "ix_audit_logs_module_action",
        "audit_logs",
        ["module", "action"],
    )

    # ── 登录日志表（SPEC 18.1）──
    # 不包含密码和 Token 列（SPEC 18.1 / 12.4）。
    op.create_table(
        "login_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=True),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("session_id", sa.String(100), nullable=True),
        sa.Column("ip_address", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("result", sa.String(50), nullable=False),
        sa.Column("failure_reason", sa.String(200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_login_logs"),
    )

    # 索引 — 支持 G3 登录日志查询（SPEC 18.1: 按用户、时间、IP 和结果）。
    op.create_index(
        "ix_login_logs_occurred_at",
        "login_logs",
        ["occurred_at"],
    )
    op.create_index(
        "ix_login_logs_user_id",
        "login_logs",
        ["user_id"],
    )
    op.create_index(
        "ix_login_logs_ip_address",
        "login_logs",
        ["ip_address"],
    )


def downgrade() -> None:
    """删除登录日志和审计日志表及相关索引。"""

    op.drop_index("ix_login_logs_ip_address", table_name="login_logs")
    op.drop_index("ix_login_logs_user_id", table_name="login_logs")
    op.drop_index("ix_login_logs_occurred_at", table_name="login_logs")
    op.drop_table("login_logs")

    op.drop_index("ix_audit_logs_module_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_occurred_at", table_name="audit_logs")
    op.drop_table("audit_logs")
