"""审计模块迁移：创建 audit_logs、login_logs 表

创建审计模块的数据表（SPEC §18.1–18.2）：

- ``audit_logs``：操作审计记录表，含操作者身份、时间、模块、动作、
  目标资源类型/ID、结果、Request ID 和变更差异（JSON）
- ``login_logs``：登录日志表，含用户、会话、IP、User-Agent、时间、
  结果和失败原因

审计记录为不可变追加日志——表结构不提供 update/delete 的 ORM 操作路径
（SPEC §18.2：审计日志不通过普通业务 CRUD 修改）。

``down_revision`` 指向 ``0006``（RBAC 表），确保单头约束
（SPEC §5.5、§8.2）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08 00:00:02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 audit_logs、login_logs 表。"""
    # -- audit_logs 表（SPEC §18.2）--
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_display_name", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("module", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=True),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("resource_display_name", sa.String(length=200), nullable=True),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("diff", sa.Text(), nullable=True),
    )
    # 按模块和动作查询索引（TASK-026 审计查询使用）
    op.create_index(
        "ix_audit_logs_module_action",
        "audit_logs",
        ["module", "action"],
    )
    # 按操作者查询索引
    op.create_index(
        "ix_audit_logs_actor_id",
        "audit_logs",
        ["actor_id"],
    )
    # 按时间查询索引（分页查询和保留清理使用）
    op.create_index(
        "ix_audit_logs_occurred_at",
        "audit_logs",
        ["occurred_at"],
    )
    # 按资源类型和标识查询索引
    op.create_index(
        "ix_audit_logs_resource",
        "audit_logs",
        ["resource_type", "resource_id"],
    )

    # -- login_logs 表（SPEC §18.1）--
    op.create_table(
        "login_logs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("failure_reason", sa.String(length=200), nullable=True),
    )
    # 按用户查询索引（SPEC §18.1：G3 按用户分页查询）
    op.create_index(
        "ix_login_logs_user_id",
        "login_logs",
        ["user_id"],
    )
    # 按时间查询索引
    op.create_index(
        "ix_login_logs_occurred_at",
        "login_logs",
        ["occurred_at"],
    )
    # 按结果查询索引
    op.create_index(
        "ix_login_logs_result",
        "login_logs",
        ["result"],
    )


def downgrade() -> None:
    """删除 audit_logs、login_logs 表。"""
    op.drop_index("ix_login_logs_result", table_name="login_logs")
    op.drop_index("ix_login_logs_occurred_at", table_name="login_logs")
    op.drop_index("ix_login_logs_user_id", table_name="login_logs")
    op.drop_table("login_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_module_action", table_name="audit_logs")
    op.drop_table("audit_logs")
