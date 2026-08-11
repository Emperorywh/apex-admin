"""备份与恢复单元测试 — SPEC 27.1 / 27.2 / 27.3.

覆盖:
  - 清单构建、哈希计算、序列化/反序列化（SPEC 27.2）
  - 滚动保留策略 7 日 + 4 周（SPEC 27.1）
  - 领域模型不变量
  - 数据库不可用时备份命令退出码非 0（SPEC 27.1: 备份失败可发现）
  - 文件一致性检查逻辑
  - Backup ID 生成唯一性
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.backup.errors import ManifestError
from app.modules.backup.manifest import (
    build_file_entries,
    compute_manifest_hash,
    find_latest_backup,
    parse_backup_set,
    read_manifest,
    serialize_backup_set,
)
from app.modules.backup.models import (
    BackupReport,
    BackupSet,
    CheckResult,
    FileManifestEntry,
)
from app.modules.backup.retention import (
    apply_retention,
    format_retention_report,
)
from app.modules.backup.service import (
    RPO_TARGET_HOURS,
    RTO_TARGET_HOURS,
    _check_file_consistency,
    _compute_file_sha256,
    _generate_backup_id,
    _parse_db_url,
)
from app.modules.file.models import FileMetadata, FileStatus

# ═══════════════════════════════════════════════════════════════════════════
# 辅助工厂
# ═══════════════════════════════════════════════════════════════════════════


pytestmark = [pytest.mark.g4, pytest.mark.unit]


def _make_file_metadata(
    *,
    file_id: object = None,
    storage_name: str = "test-file.pdf",
    size_bytes: int = 1024,
    sha256: str = "a" * 64,
) -> FileMetadata:
    """创建测试用文件元数据."""

    now = datetime.now(UTC)
    return FileMetadata(
        id=file_id or uuid4(),
        original_name="test.pdf",
        storage_name=storage_name,
        size_bytes=size_bytes,
        content_type="application/pdf",
        file_extension="pdf",
        sha256=sha256,
        status=FileStatus.READY,
        uploaded_by="test-user",
        created_at=now,
        updated_at=now,
        deleting_entered_at=None,
    )


def _make_backup_set(
    *,
    backup_id: str = "backup-test",
    created_at: datetime | None = None,
    files: list[FileManifestEntry] | None = None,
) -> BackupSet:
    """创建测试用备份集."""

    if files is None:
        files = []
    return BackupSet(
        backup_id=backup_id,
        created_at=created_at or datetime.now(UTC),
        database_dump_file="database.sql",
        files=files,
        file_count=len(files),
        total_size_bytes=sum(f.size_bytes for f in files),
        manifest_sha256=compute_manifest_hash(files),
    )


def _write_manifest(tmp_path: Path, backup_id: str, created_at: datetime) -> Path:
    """在 tmp_path 下创建一个备份集目录并写入 manifest.json."""

    backup_dir = tmp_path / backup_id
    backup_dir.mkdir(parents=True)
    backup = _make_backup_set(backup_id=backup_id, created_at=created_at)
    (backup_dir / "manifest.json").write_text(
        json.dumps(serialize_backup_set(backup), ensure_ascii=False),
        encoding="utf-8",
    )
    return backup_dir


# ═══════════════════════════════════════════════════════════════════════════
# 清单构建与哈希（SPEC 27.2）
# ═══════════════════════════════════════════════════════════════════════════


class TestManifestBuilding:
    """SPEC 27.2: 只将 READY 文件纳入备份清单。"""

    def test_build_entries_from_ready_files(self) -> None:
        """READY 文件元数据正确转换为清单条目。"""

        file_id = uuid4()
        metadata = _make_file_metadata(file_id=file_id, storage_name="abc.pdf")

        entries = build_file_entries([metadata])

        assert len(entries) == 1
        assert entries[0].file_id == str(file_id)
        assert entries[0].storage_name == "abc.pdf"
        assert entries[0].sha256 == metadata.sha256

    def test_build_entries_sorted_by_file_id(self) -> None:
        """清单条目按 file_id 排序，确保确定性。"""

        ids = [uuid4() for _ in range(5)]
        files = [_make_file_metadata(file_id=i) for i in ids]
        entries = build_file_entries(files)
        entry_ids = [e.file_id for e in entries]
        assert entry_ids == sorted(entry_ids)

    def test_manifest_hash_deterministic(self) -> None:
        """相同文件集产生相同清单哈希。"""

        entries1 = [
            FileManifestEntry(
                file_id="a",
                original_name="a.pdf",
                storage_name="a.pdf",
                size_bytes=100,
                sha256="aaa",
                content_type="application/pdf",
                file_extension="pdf",
            ),
        ]
        entries2 = [
            FileManifestEntry(
                file_id="a",
                original_name="a.pdf",
                storage_name="a.pdf",
                size_bytes=100,
                sha256="aaa",
                content_type="application/pdf",
                file_extension="pdf",
            ),
        ]
        assert compute_manifest_hash(entries1) == compute_manifest_hash(entries2)

    def test_manifest_hash_differs_on_change(self) -> None:
        """文件集变更时清单哈希不同（防篡改）。"""

        entry = FileManifestEntry(
            file_id="a",
            original_name="a.pdf",
            storage_name="a.pdf",
            size_bytes=100,
            sha256="aaa",
            content_type="application/pdf",
            file_extension="pdf",
        )
        modified = FileManifestEntry(
            file_id="a",
            original_name="a.pdf",
            storage_name="a.pdf",
            size_bytes=200,  # 修改大小
            sha256="aaa",
            content_type="application/pdf",
            file_extension="pdf",
        )
        assert compute_manifest_hash([entry]) != compute_manifest_hash([modified])


# ═══════════════════════════════════════════════════════════════════════════
# 序列化与反序列化（SPEC 27.2）
# ═══════════════════════════════════════════════════════════════════════════


class TestBackupSetSerialization:
    """SPEC 27.2: 备份集序列化包含 Backup ID/时间/文件清单/数量/大小/哈希。"""

    def test_roundtrip(self) -> None:
        """序列化与反序列化保持数据一致。"""

        now = datetime.now(UTC)
        entries = [
            FileManifestEntry(
                file_id=str(uuid4()),
                original_name="report.pdf",
                storage_name="abc.pdf",
                size_bytes=12345,
                sha256="b" * 64,
                content_type="application/pdf",
                file_extension="pdf",
            ),
        ]
        original = BackupSet(
            backup_id="backup-20260812-143052-a1b2c3d4",
            created_at=now,
            database_dump_file="database.sql",
            files=entries,
            file_count=1,
            total_size_bytes=12345,
            manifest_sha256=compute_manifest_hash(entries),
        )

        serialized = serialize_backup_set(original)
        assert serialized["backup_id"] == original.backup_id
        assert serialized["file_count"] == 1
        assert serialized["total_size_bytes"] == 12345
        assert serialized["manifest_sha256"] == original.manifest_sha256

        parsed = parse_backup_set(serialized)
        assert parsed.backup_id == original.backup_id
        assert parsed.file_count == 1
        assert parsed.manifest_sha256 == original.manifest_sha256

    def test_parse_missing_field_raises(self) -> None:
        """缺少必需字段时抛出 ManifestError。"""

        with pytest.raises(ManifestError):
            parse_backup_set({"backup_id": "test"})  # type: ignore[arg-type]

    def test_read_manifest_missing_file(self, tmp_path: Path) -> None:
        """manifest.json 不存在时抛出 ManifestError。"""

        with pytest.raises(ManifestError):
            read_manifest(tmp_path / "nonexistent")

    def test_read_manifest_invalid_json(self, tmp_path: Path) -> None:
        """无效 JSON 抛出 ManifestError。"""

        backup_dir = tmp_path / "bad"
        backup_dir.mkdir()
        (backup_dir / "manifest.json").write_text("not json", encoding="utf-8")
        with pytest.raises(ManifestError):
            read_manifest(backup_dir)


# ═══════════════════════════════════════════════════════════════════════════
# 滚动保留策略（SPEC 27.1）
# ═══════════════════════════════════════════════════════════════════════════


class TestRetention:
    """SPEC 27.1: 至少保留最近 7 个日备份和最近 4 个周备份。"""

    def test_keeps_7_daily(self, tmp_path: Path) -> None:
        """7 天内每天一个备份全部保留。"""

        now = datetime.now(UTC)
        for i in range(7):
            day = now - timedelta(days=i)
            _write_manifest(tmp_path, f"backup-day-{i}", day)

        result = apply_retention(
            tmp_path,
            daily_retention=7,
            weekly_retention=4,
        )

        assert len(result.keep_dirs) == 7
        assert len(result.delete_dirs) == 0

    def test_deletes_old_beyond_retention(self, tmp_path: Path) -> None:
        """超过 7 日和 4 周保留的备份被删除。"""

        now = datetime.now(UTC)
        # 创建 12 个备份，每周一个（跨 12 个不同 ISO 周和 12 个不同日期）
        for i in range(12):
            ts = now - timedelta(weeks=i)
            _write_manifest(tmp_path, f"backup-w{i}", ts)

        result = apply_retention(
            tmp_path,
            daily_retention=7,
            weekly_retention=4,
        )

        keep_names = {d.name for d in result.keep_dirs}
        delete_names = {d.name for d in result.delete_dirs}

        # 日保留取前 7 个唯一日期 → w0..w6 保留
        assert "backup-w0" in keep_names
        assert "backup-w6" in keep_names
        # w7..w11 既不在前 7 个日期也不在前 4 周内 → 删除
        assert len(result.delete_dirs) == 5
        assert "backup-w11" in delete_names

    def test_weekly_retention_keeps_one_per_week(self, tmp_path: Path) -> None:
        """每周保留最新一个备份。"""

        now = datetime.now(UTC)
        # 在 4 周前创建多个备份（同一天）
        three_weeks_ago = now - timedelta(weeks=3)
        for i in range(3):
            ts = three_weeks_ago + timedelta(hours=i)
            _write_manifest(tmp_path, f"backup-week3-{i}", ts)

        result = apply_retention(
            tmp_path,
            daily_retention=7,
            weekly_retention=4,
        )

        # 这 3 个备份在同一天，超出 7 日保留，
        # 但该 ISO 周只保留最新一个（其余被删除）
        week3_dirs = [
            d for d in result.keep_dirs + result.delete_dirs if "week3" in d.name
        ]
        assert len(week3_dirs) == 3
        kept_week3 = [d for d in result.keep_dirs if "week3" in d.name]
        assert len(kept_week3) == 1  # 每周只保留一个

    def test_empty_directory(self, tmp_path: Path) -> None:
        """空目录返回空结果。"""

        result = apply_retention(tmp_path, daily_retention=7, weekly_retention=4)
        assert len(result.keep_dirs) == 0
        assert len(result.delete_dirs) == 0

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """不存在的目录返回空结果。"""

        result = apply_retention(
            tmp_path / "noexist",
            daily_retention=7,
            weekly_retention=4,
        )
        assert len(result.keep_dirs) == 0
        assert len(result.delete_dirs) == 0

    def test_format_report(self) -> None:
        """报告格式化不抛异常。"""

        from app.modules.backup.retention import RetentionResult

        result = RetentionResult(
            keep_dirs=[Path("/keep/a")],
            delete_dirs=[Path("/delete/b")],
        )
        text = format_retention_report(result)
        assert "保留" in text
        assert "待删除" in text


# ═══════════════════════════════════════════════════════════════════════════
# Backup ID 生成（SPEC 27.2）
# ═══════════════════════════════════════════════════════════════════════════


class TestBackupIdGeneration:
    """SPEC 27.2: 每个备份集具有唯一 Backup ID。"""

    def test_format(self) -> None:
        """Backup ID 格式正确。"""

        now = datetime(2026, 8, 12, 14, 30, 52, tzinfo=UTC)
        backup_id = _generate_backup_id(now)
        assert backup_id.startswith("backup-20260812-143052-")

    def test_uniqueness(self) -> None:
        """连续生成产生不同 ID。"""

        now = datetime.now(UTC)
        ids = {_generate_backup_id(now) for _ in range(100)}
        assert len(ids) == 100


# ═══════════════════════════════════════════════════════════════════════════
# 文件一致性检查（SPEC 27.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestFileConsistency:
    """SPEC 27.3: 文件一致性检查。"""

    def test_all_files_match(self, tmp_path: Path) -> None:
        """全部文件 SHA-256 一致时检查通过。"""

        content = b"hello world"
        import hashlib

        sha = hashlib.sha256(content).hexdigest()

        backup_dir = tmp_path / "backup-set"
        files_dir = backup_dir / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "abc.pdf").write_bytes(content)

        entry = FileManifestEntry(
            file_id="test-id",
            original_name="test.pdf",
            storage_name="abc.pdf",
            size_bytes=len(content),
            sha256=sha,
            content_type="application/pdf",
            file_extension="pdf",
        )
        backup_set = _make_backup_set(files=[entry])

        result = _check_file_consistency(backup_dir, backup_set)
        assert result.passed

    def test_missing_file(self, tmp_path: Path) -> None:
        """物理文件缺失时检查失败。"""

        backup_dir = tmp_path / "backup-set"
        (backup_dir / "files").mkdir(parents=True)

        entry = FileManifestEntry(
            file_id="test-id",
            original_name="test.pdf",
            storage_name="missing.pdf",
            size_bytes=100,
            sha256="x" * 64,
            content_type="application/pdf",
            file_extension="pdf",
        )
        backup_set = _make_backup_set(files=[entry])

        result = _check_file_consistency(backup_dir, backup_set)
        assert not result.passed
        assert "缺失" in result.detail

    def test_hash_mismatch(self, tmp_path: Path) -> None:
        """SHA-256 不匹配时检查失败。"""

        backup_dir = tmp_path / "backup-set"
        files_dir = backup_dir / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "abc.pdf").write_bytes(b"actual content")

        entry = FileManifestEntry(
            file_id="test-id",
            original_name="test.pdf",
            storage_name="abc.pdf",
            size_bytes=100,
            sha256="0" * 64,  # 错误哈希
            content_type="application/pdf",
            file_extension="pdf",
        )
        backup_set = _make_backup_set(files=[entry])

        result = _check_file_consistency(backup_dir, backup_set)
        assert not result.passed
        assert "不匹配" in result.detail

    def test_empty_file_list_passes(self, tmp_path: Path) -> None:
        """空文件列表检查通过。"""

        backup_dir = tmp_path / "backup-set"
        (backup_dir / "files").mkdir(parents=True)
        backup_set = _make_backup_set(files=[])

        result = _check_file_consistency(backup_dir, backup_set)
        assert result.passed

    def test_compute_sha256(self, tmp_path: Path) -> None:
        """SHA-256 计算正确。"""

        import hashlib

        file_path = tmp_path / "test.bin"
        content = b"test content for sha256"
        file_path.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _compute_file_sha256(file_path) == expected


# ═══════════════════════════════════════════════════════════════════════════
# find_latest_backup（SPEC 27.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestFindLatestBackup:
    """SPEC 27.3: 验证最新备份。"""

    def test_finds_most_recent(self, tmp_path: Path) -> None:
        """正确找到 created_at 最新的备份集。"""

        now = datetime.now(UTC)
        _write_manifest(
            tmp_path,
            "backup-old",
            now - timedelta(days=2),
        )
        _write_manifest(
            tmp_path,
            "backup-new",
            now,
        )

        latest = find_latest_backup(tmp_path)
        assert latest.name == "backup-new"

    def test_no_backup_raises(self, tmp_path: Path) -> None:
        """无备份集时抛出 ManifestError。"""

        with pytest.raises(ManifestError):
            find_latest_backup(tmp_path)

    def test_nonexistent_dir_raises(self, tmp_path: Path) -> None:
        """目录不存在时抛出 ManifestError。"""

        with pytest.raises(ManifestError):
            find_latest_backup(tmp_path / "noexist")


# ═══════════════════════════════════════════════════════════════════════════
# URL 解析
# ═══════════════════════════════════════════════════════════════════════════


class TestParseDbUrl:
    """数据库 URL 解析。"""

    def test_standard_url(self) -> None:
        """标准 URL 正确解析。"""

        params = _parse_db_url("postgresql+psycopg://testuser@127.0.0.1:55432/postgres")
        assert params["host"] == "127.0.0.1"
        assert params["port"] == 55432
        assert params["user"] == "testuser"
        assert params["dbname"] == "postgres"

    def test_url_with_password(self) -> None:
        """带密码的 URL 正确解析。"""

        params = _parse_db_url(
            "postgresql+psycopg://testuser:secret@10.0.0.1:5432/testdb",
        )
        assert params["host"] == "10.0.0.1"
        assert params["port"] == 5432
        assert params["user"] == "testuser"
        assert params["password"] == "secret"
        assert params["dbname"] == "testdb"


# ═══════════════════════════════════════════════════════════════════════════
# 领域模型不变量
# ═══════════════════════════════════════════════════════════════════════════


class TestModelsImmutable:
    """领域模型不可变。"""

    def test_backup_set_frozen(self) -> None:
        """BackupSet 不可变。"""

        backup = _make_backup_set()
        with pytest.raises(AttributeError):
            backup.backup_id = "changed"  # type: ignore[misc]

    def test_file_manifest_entry_frozen(self) -> None:
        """FileManifestEntry 不可变。"""

        entry = FileManifestEntry(
            file_id="a",
            original_name="a.pdf",
            storage_name="a.pdf",
            size_bytes=100,
            sha256="x" * 64,
            content_type="application/pdf",
            file_extension="pdf",
        )
        with pytest.raises(AttributeError):
            entry.size_bytes = 200  # type: ignore[misc]

    def test_backup_report_defaults(self) -> None:
        """BackupReport 默认值正确。"""

        now = datetime.now(UTC)
        passed = CheckResult(passed=True, detail="ok")
        report = BackupReport(
            backup_id="test",
            started_at=now,
            finished_at=now,
            actual_rpo_hours=0.0,
            actual_rto_hours=0.0,
            rpo_target_hours=RPO_TARGET_HOURS,
            rto_target_hours=RTO_TARGET_HOURS,
            migration_check=passed,
            integrity_check=passed,
            file_check=passed,
            overall_passed=True,
        )
        assert report.failure_reason is None
        assert report.overall_passed is True


# ═══════════════════════════════════════════════════════════════════════════
# 数据库不可用时备份失败（SPEC 27.1: 备份失败可发现）
# ═══════════════════════════════════════════════════════════════════════════


class TestBackupFailureDiscovery:
    """SPEC 27.1: 数据库不可用时备份命令退出码非 0。"""

    def test_create_backup_db_unavailable_raises(
        self,
        tmp_path: Path,
    ) -> None:
        """数据库不可用时 create_backup 抛出 BackupCreationError。"""

        import asyncio

        from app.modules.backup.errors import BackupCreationError
        from app.modules.backup.service import create_backup

        # 使用一个不可达的端口确保连接失败
        bad_url = "postgresql+psycopg://nobody@127.0.0.1:1/none"

        with pytest.raises(BackupCreationError, match="数据库不可用"):
            asyncio.run(
                create_backup(
                    database_url=bad_url,
                    output_dir=tmp_path / "backups",
                    storage_root=str(tmp_path / "files"),
                    daily_retention=7,
                    weekly_retention=4,
                ),
            )
