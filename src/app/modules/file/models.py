"""文件管理领域实体与状态枚举 — SPEC 19.1 / 19.3 / 19.4 / 5.2.

SPEC 19.1: 文件元数据包含原始名称、存储名称、大小、类型、哈希、上传者和时间。
SPEC 19.3: 文件状态机 PENDING → READY → DELETING → DELETED，
            PENDING/READY 可转 FAILED。
SPEC 19.4: 文件引用包含业务模块编码、资源类型和资源 ID。

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class FileStatus(StrEnum):
    """文件元数据状态枚举 — SPEC 19.3.

    状态转换图::

        PENDING → READY → DELETING → DELETED
           │         │
           └─────────┴────→ FAILED

    属性:
        PENDING:  上传流式写入临时文件后、校验通过后创建的初始状态。
        READY:    临时文件已原子 rename 到最终路径，可供下载。
        DELETING: 删除已发起，确认无活动业务引用，等待物理删除延迟到期。
        DELETED:  物理文件已删除，元数据保留用于审计。
        FAILED:   上传失败或物理文件丢失，终态。
    """

    PENDING = "pending"
    READY = "ready"
    DELETING = "deleting"
    DELETED = "deleted"
    FAILED = "failed"


@dataclass(frozen=True)
class FileMetadata:
    """文件元数据领域实体 — SPEC 19.1 / 19.3.

    SPEC 19.1:
      - 文件实际路径与业务访问标识分离。
      - 文件名使用安全生成规则（存储名称由服务器生成，非用户输入）。
      - 元数据包含原始名称、存储名称、大小、类型、哈希、上传者和时间。

    SPEC 19.3:
      - 状态机管理文件生命周期。

    属性:
        id:              全局唯一标识（UUID）——业务访问标识。
        original_name:   上传时的原始文件名（用户输入，仅用于展示）。
        storage_name:    服务器生成的安全存储文件名（UUID 基）。
        size_bytes:      文件大小（字节）。
        content_type:    声明的 MIME 类型。
        file_extension:  文件扩展名（小写，不含点）。
        sha256:          文件内容 SHA-256 摘要（十六进制）。
        status:          文件状态（FileStatus）。
        uploaded_by:     上传者标识。
        created_at:      元数据创建时间（UTC）。
        updated_at:      状态变更时间（UTC）。
        deleting_entered_at: 进入 DELETING 状态的时间（UTC），用于延迟物理删除。
    """

    id: UUID
    original_name: str
    storage_name: str
    size_bytes: int
    content_type: str
    file_extension: str
    sha256: str
    status: FileStatus
    uploaded_by: str | None
    created_at: datetime
    updated_at: datetime
    deleting_entered_at: datetime | None = None


@dataclass(frozen=True)
class FileReference:
    """文件业务引用领域实体 — SPEC 19.4.

    SPEC 19.4:
      - 业务模块只保存或传递稳定 File ID。
      - 文件引用必须包含业务模块编码、资源类型和资源 ID。
      - 具有防重复唯一约束。

    属性:
        id:           全局唯一标识（UUID）。
        file_id:      被引用的文件元数据 ID。
        module_code:  引用方业务模块编码（如 ``"example"``）。
        resource_type: 引用方资源类型（如 ``"example_item"``）。
        resource_id:  引用方资源标识。
        created_at:   引用创建时间（UTC）。
    """

    id: UUID
    file_id: UUID
    module_code: str
    resource_type: str
    resource_id: str
    created_at: datetime
