"""权限点注册表：创建 permission_points 表

创建 RBAC 模块的权限点注册表（SPEC §13.1、§25.2）：

- ``permission_points``：存储所有启用模块声明的权限点（编码、描述、模块编码）

权限点通过 ModuleDefinition 声明，``sync-permissions`` 命令幂等同步到此表
（SPEC §25.2）。``role_permissions`` 以稳定编码引用权限点，
此表为权限点的权威注册来源。

``down_revision`` 指向 ``0007``（audit 表），确保单头约束。

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08 00:00:02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 permission_points 表。"""
    op.create_table(
        "permission_points",
        sa.Column("code", sa.String(length=100), primary_key=True, nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("module_code", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # 按模块编码查询索引
    op.create_index(
        "ix_permission_points_module_code",
        "permission_points",
        ["module_code"],
    )


def downgrade() -> None:
    """删除 permission_points 表。"""
    op.drop_index("ix_permission_points_module_code", table_name="permission_points")
    op.drop_table("permission_points")
