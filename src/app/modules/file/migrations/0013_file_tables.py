"""文件管理模块迁移 — 创建 ``file_metadata`` / ``file_references`` 表.

SPEC 8.2:
  - 所有表结构变更必须通过迁移文件交付。
  - 所有启用模块共同组成唯一 Alembic head。
  - 每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head。

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 19.1:
  - ``file_metadata`` 表存储文件元数据。

SPEC 19.4:
  - ``file_references`` 表存储业务引用记录，
    ``file_id`` + ``module_code`` + ``resource_type`` + ``resource_id``
    复合唯一（防重复 retain）。

Revision ID: 0013_file_tables
Revises: 0012_dict_tables
Create Date: 2026-08-11 00:00:08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0013_file_tables"
down_revision: str | Sequence[str] | None = "0012_dict_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建文件元数据和文件引用表及相关索引。"""

    # 文件元数据表 — SPEC 19.1
    op.create_table(
        "file_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_name", sa.String(500), nullable=False),
        sa.Column("storage_name", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_extension", sa.String(20), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("uploaded_by", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "deleting_entered_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_file_metadata_status",
        "file_metadata",
        ["status"],
    )
    op.create_index(
        "ix_file_metadata_uploaded_by",
        "file_metadata",
        ["uploaded_by"],
    )
    op.create_index(
        "ix_file_metadata_sha256",
        "file_metadata",
        ["sha256"],
    )

    # 文件引用表 — SPEC 19.4
    op.create_table(
        "file_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("module_code", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["file_metadata.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id",
            "module_code",
            "resource_type",
            "resource_id",
            name="uq_file_references_fid_mod_type_res",
        ),
    )

    op.create_index(
        "ix_file_references_file_id",
        "file_references",
        ["file_id"],
    )
    op.create_index(
        "ix_file_references_module_resource",
        "file_references",
        ["module_code", "resource_type", "resource_id"],
    )


def downgrade() -> None:
    """删除文件相关表.

    不可逆——文件数据需要手动备份恢复。
    """

    op.drop_index(
        "ix_file_references_module_resource",
        table_name="file_references",
    )
    op.drop_index("ix_file_references_file_id", table_name="file_references")
    op.drop_table("file_references")

    op.drop_index("ix_file_metadata_sha256", table_name="file_metadata")
    op.drop_index("ix_file_metadata_uploaded_by", table_name="file_metadata")
    op.drop_index("ix_file_metadata_status", table_name="file_metadata")
    op.drop_table("file_metadata")
