"""文件管理 ORM 模型 — SPEC 8.3 / 19.1 / 19.3 / 19.4.

SPEC 8.3 数据建模规范:
  - 主键为 UUID。
  - 时间字段使用 ``timestamptz``，统一 UTC。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 19.1:
  - ``file_metadata`` 表存储文件元数据。

SPEC 19.3:
  - ``status`` 字段记录状态机当前状态。

SPEC 19.4:
  - ``file_references`` 表存储业务引用记录，
    ``module_code`` + ``resource_type`` + ``resource_id`` + ``file_id``
    复合唯一（防重复 retain）。

ORM 模型只在 Infrastructure 层使用，不泄漏到 Application 或 API 层（SPEC 5.2）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class FileMetadataORM(Base):
    """文件元数据 ORM 模型 — 映射 ``file_metadata`` 表（SPEC 19.1 / 19.3）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - 时间字段使用 ``DateTime(timezone=True)``。

    SPEC 19.1:
      - ``original_name`` 为用户上传的原始文件名。
      - ``storage_name`` 为服务器生成的安全存储文件名。
      - ``sha256`` 为文件内容 SHA-256 摘要。

    SPEC 19.3:
      - ``status`` 记录状态机当前状态。
      - ``deleting_entered_at`` 记录进入 DELETING 的时间，用于延迟物理删除。
    """

    __tablename__ = "file_metadata"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(20), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    deleting_entered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_file_metadata_status", status),
        Index("ix_file_metadata_uploaded_by", uploaded_by),
        Index("ix_file_metadata_sha256", sha256),
    )


class FileReferenceORM(Base):
    """文件业务引用 ORM 模型 — 映射 ``file_references`` 表（SPEC 19.4）.

    SPEC 19.4:
      - 业务模块只保存或传递稳定 File ID。
      - 文件引用包含业务模块编码、资源类型和资源 ID。
      - 具有防重复唯一约束。

    ``file_id`` + ``module_code`` + ``resource_type`` + ``resource_id``
    复合唯一约束防止重复 retain。
    """

    __tablename__ = "file_references"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("file_metadata.id", ondelete="CASCADE"),
        nullable=False,
    )
    module_code: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "module_code",
            "resource_type",
            "resource_id",
            name="uq_file_references_fid_mod_type_res",
        ),
        Index("ix_file_references_file_id", file_id),
        Index(
            "ix_file_references_module_resource",
            module_code,
            resource_type,
            resource_id,
        ),
    )
