"""备份与恢复领域模型 — SPEC 27.1 / 27.2 / 27.3.

SPEC 27.2:
  - 每个备份集具有唯一 Backup ID、数据库备份时间、文件清单、
    文件数量、总大小和清单哈希。

SPEC 27.3:
  - 恢复演练记录 Backup ID、开始时间、结束时间、实际 RPO、
    实际 RTO、检查结果和失败原因。

领域模型是不可变 ``frozen dataclass``，不依赖基础设施类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class FileManifestEntry:
    """文件清单条目 — 对应一个 READY 文件（SPEC 27.2）.

    属性:
        file_id:        文件元数据 UUID（稳定标识）。
        original_name:  上传时的原始文件名。
        storage_name:   服务器生成的安全存储文件名。
        size_bytes:     文件大小（字节）。
        sha256:         文件内容 SHA-256 摘要（十六进制）。
        content_type:   声明的 MIME 类型。
        file_extension: 文件扩展名（小写，不含点）。
    """

    file_id: str
    original_name: str
    storage_name: str
    size_bytes: int
    sha256: str
    content_type: str
    file_extension: str


@dataclass(frozen=True)
class BackupSet:
    """备份集 — SPEC 27.1 / 27.2.

    一个备份集包含：
      - 唯一 Backup ID
      - 创建时间
      - 数据库逻辑全量备份文件名
      - READY 文件清单（条目列表、文件数量、总大小）
      - 清单哈希（SHA-256，防篡改）

    属性:
        backup_id:          全局唯一备份标识。
        created_at:         备份创建时间（UTC）。
        database_dump_file: pg_dump 输出文件名（相对备份集目录）。
        files:              READY 文件清单条目列表。
        file_count:         文件总数。
        total_size_bytes:   全部文件总大小（字节）。
        manifest_sha256:    文件清单的 SHA-256 摘要（防篡改）。
    """

    backup_id: str
    created_at: datetime
    database_dump_file: str
    files: list[FileManifestEntry] = field(default_factory=list)
    file_count: int = 0
    total_size_bytes: int = 0
    manifest_sha256: str = ""


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果 — SPEC 27.3.

    属性:
        passed:  检查是否通过。
        detail:  检查详情（通过时为摘要，失败时为问题描述）。
    """

    passed: bool
    detail: str


@dataclass(frozen=True)
class BackupReport:
    """恢复演练报告 — SPEC 27.3.

    SPEC 27.3: "恢复演练记录 Backup ID、开始时间、结束时间、实际 RPO、
    实际 RTO、检查结果和失败原因"。
    SPEC 27.3: "实际 RPO 或 RTO 超过 27.1 目标时，G4 验收失败"。

    属性:
        backup_id:        被验证的备份集 ID。
        started_at:       演练开始时间（UTC）。
        finished_at:      演练结束时间（UTC）。
        actual_rpo_hours: 实际 RPO（小时）— 备份创建到演练开始的时间差。
        actual_rto_hours: 实际 RTO（小时）— 演练开始到结束的耗时。
        rpo_target_hours: RPO 目标（小时，默认 24）。
        rto_target_hours: RTO 目标（小时，默认 4）。
        migration_check:  迁移版本检查结果。
        integrity_check:  数据完整性检查结果。
        file_check:       文件一致性检查结果。
        overall_passed:   全部检查是否通过且 RPO/RTO 未超标。
        failure_reason:   失败原因（通过时为 None）。
    """

    backup_id: str
    started_at: datetime
    finished_at: datetime
    actual_rpo_hours: float
    actual_rto_hours: float
    rpo_target_hours: float
    rto_target_hours: float
    migration_check: CheckResult
    integrity_check: CheckResult
    file_check: CheckResult
    overall_passed: bool
    failure_reason: str | None = None
