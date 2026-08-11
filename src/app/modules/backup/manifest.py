"""备份文件清单构建与序列化 — SPEC 27.2.

SPEC 27.2:
  - 只将 19.3 中 READY 文件及备份清单纳入文件备份。
  - 每个备份集具有唯一 Backup ID、文件清单、文件数量、总大小和清单哈希。
  - 临时文件、PENDING、FAILED、DELETING 和 DELETED 文件不进入正式备份。
  - 文件清单哈希防篡改。

清单使用规范化 JSON 序列化（键排序、无多余空白）计算 SHA-256，
确保相同文件集产生相同清单哈希，便于篡改检测。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from app.modules.backup.errors import ManifestError
from app.modules.backup.models import BackupSet, FileManifestEntry

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from app.modules.file.models import FileMetadata


def build_file_entries(
    ready_files: list[FileMetadata],
) -> list[FileManifestEntry]:
    """从 READY 文件元数据构建清单条目列表.

    SPEC 27.2: 只将 READY 文件纳入备份清单。

    参数:
        ready_files: READY 状态的文件元数据列表。

    返回:
        文件清单条目列表，按 file_id 排序以确保确定性。
    """

    entries = [
        FileManifestEntry(
            file_id=str(f.id),
            original_name=f.original_name,
            storage_name=f.storage_name,
            size_bytes=f.size_bytes,
            sha256=f.sha256,
            content_type=f.content_type,
            file_extension=f.file_extension,
        )
        for f in ready_files
    ]
    entries.sort(key=lambda e: e.file_id)
    return entries


def compute_manifest_hash(entries: list[FileManifestEntry]) -> str:
    """计算文件清单的 SHA-256 摘要 — SPEC 27.2: 清单哈希防篡改.

    使用规范化 JSON 序列化（键排序、紧凑格式），
    确保相同文件集产生相同摘要。

    参数:
        entries: 文件清单条目列表。

    返回:
        SHA-256 十六进制摘要。
    """

    canonical = json.dumps(
        [e.__dict__ for e in entries],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def serialize_backup_set(backup: BackupSet) -> dict[str, object]:
    """将备份集序列化为可写 JSON 字典.

    输出结构包含 Backup ID、创建时间、数据库备份文件名、
    文件清单、文件数量、总大小和清单哈希。
    """

    return {
        "backup_id": backup.backup_id,
        "created_at": backup.created_at.isoformat(),
        "database_dump_file": backup.database_dump_file,
        "files": [e.__dict__ for e in backup.files],
        "file_count": backup.file_count,
        "total_size_bytes": backup.total_size_bytes,
        "manifest_sha256": backup.manifest_sha256,
    }


def parse_backup_set(data: dict[str, object]) -> BackupSet:
    """从 JSON 字典解析备份集.

    参数:
        data: JSON 解析后的字典。

    返回:
        BackupSet 领域实例。

    异常:
        ManifestError: 字段缺失或格式不合法。
    """

    from datetime import datetime

    try:
        backup_id = str(data["backup_id"])
        created_at_str = str(data["created_at"])
        created_at = datetime.fromisoformat(created_at_str)
        database_dump_file = str(data["database_dump_file"])
        file_count = int(str(data["file_count"]))
        total_size_bytes = int(str(data["total_size_bytes"]))
        manifest_sha256 = str(data["manifest_sha256"])
        raw_files: list[dict[str, object]] = data.get("files", [])  # type: ignore[assignment]
        files = [
            FileManifestEntry(
                file_id=str(f["file_id"]),
                original_name=str(f["original_name"]),
                storage_name=str(f["storage_name"]),
                size_bytes=int(str(f["size_bytes"])),
                sha256=str(f["sha256"]),
                content_type=str(f["content_type"]),
                file_extension=str(f["file_extension"]),
            )
            for f in raw_files
        ]
    except (KeyError, ValueError, TypeError) as exc:
        msg = f"备份清单格式不合法: {exc}"
        raise ManifestError(msg) from exc

    return BackupSet(
        backup_id=backup_id,
        created_at=created_at,
        database_dump_file=database_dump_file,
        files=files,
        file_count=file_count,
        total_size_bytes=total_size_bytes,
        manifest_sha256=manifest_sha256,
    )


def read_manifest(backup_dir: Path) -> BackupSet:
    """从备份集目录读取并解析 manifest.json.

    参数:
        backup_dir: 包含 manifest.json 的备份集目录。

    返回:
        BackupSet 领域实例。

    异常:
        ManifestError: manifest.json 不存在或格式不合法。
    """

    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        msg = f"备份清单不存在: {manifest_path}"
        raise ManifestError(msg)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"备份清单 JSON 解析失败: {exc}"
        raise ManifestError(msg) from exc
    return parse_backup_set(data)


def find_latest_backup(output_dir: Path) -> Path:
    """在输出目录中查找最新的备份集目录.

    扫描 output_dir 下的子目录，读取各自的 manifest.json，
    返回 created_at 最新的备份集目录路径。

    参数:
        output_dir: 包含多个备份集的输出目录。

    返回:
        最新备份集的目录路径。

    异常:
        ManifestError: 无备份集或清单读取失败。
    """

    if not output_dir.exists():
        msg = f"备份输出目录不存在: {output_dir}"
        raise ManifestError(msg)

    candidates: list[tuple[datetime, Path]] = []
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.exists():
            continue
        backup = read_manifest(child)
        candidates.append((backup.created_at, child))

    if not candidates:
        msg = f"输出目录中无有效备份集: {output_dir}"
        raise ManifestError(msg)

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]
