"""初始迁移 — 建立全局基线.

G1 阶段无业务模块表结构（SPEC nonGoals: 不实现任何业务模块表结构）。
此迁移作为全局 revision 图的唯一 head，后续模块迁移的
``down_revision`` 指向此 revision。

SPEC 8.2:
  - 所有启用模块共同组成唯一 Alembic head。
  - 提供全新数据库初始化流程。

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-10 00:00:00
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """G1 基线迁移 — 无业务表，仅建立 revision 起点。"""


def downgrade() -> None:
    """不可逆迁移 — 初始基线不可 downgrade。"""
