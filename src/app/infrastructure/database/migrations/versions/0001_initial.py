"""G1 Core Ready 初始基线迁移

建立 Alembic 全局 revision 图的根节点。G1 基座不包含业务表结构——
各业务模块在自身迁移文件中创建表，``down_revision`` 指向生成时的全局 head
（SPEC §5.5、§8.2）。

Revision ID: 0001
Revises:
Create Date: 2026-08-07 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """G1 基线迁移——无 DDL 操作。

    ``alembic_version`` 表由 Alembic 在首次 ``upgrade head`` 时自动创建，
    无需在迁移中手动建表。后续业务模块迁移在此基础上追加结构变更。
    """


def downgrade() -> None:
    """基线迁移不可降级。

    SPEC §8.2：不要求提供自动 downgrade。
    """
