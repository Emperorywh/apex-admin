"""文件管理请求与响应 Schema — SPEC 9.2 / 9.3 / 19.2.

SPEC 9.2: 创建、全量更新请求拒绝未知字段（``extra="forbid"``）。
SPEC 9.3: JSON 字段统一 snake_case。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel


class FileMetadataResponse(BaseModel):
    """文件元数据响应模型 — SPEC 9.3 / 19.1."""

    model_config = {"extra": "forbid"}

    id: UUID
    original_name: str
    storage_name: str
    size_bytes: int
    content_type: str
    file_extension: str
    sha256: str
    status: str
    uploaded_by: str | None
    created_at: datetime
    updated_at: datetime
    deleting_entered_at: datetime | None = None
