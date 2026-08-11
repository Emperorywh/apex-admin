"""备份与恢复集成测试 — SPEC 27.1 / 27.2 / 27.3.

使用真实 PostgreSQL 实例验证:
  - backup create: pg_dump 全量 + READY 文件清单 + 保留清理
  - backup verify: 隔离库恢复 + 迁移版本/完整性/文件一致性检查 + 演练报告
  - 数据库不可用时退出码非 0（SPEC 27.1: 备份失败可发现）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.modules.backup.errors import BackupCreationError
from app.modules.backup.manifest import read_manifest
from app.modules.backup.service import create_backup, verify_backup

if TYPE_CHECKING:
    from collections.abc import Iterator


# ── 迁移 ───────────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from alembic import command

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_tables(database_url: str) -> None:
    """清理文件表。"""

    from sqlalchemy import text

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM file_references"))
            await conn.execute(text("DELETE FROM file_metadata"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移。"""

    asyncio.run(_apply_migrations(database_url))
    yield database_url
    asyncio.run(_cleanup_tables(database_url))


# ═══════════════════════════════════════════════════════════════════════════
# 备份创建（SPEC 27.1 / 27.2）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.integration
class TestBackupCreate:
    """SPEC 27.1 / 27.2: 备份创建。"""

    async def test_create_backup_success(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """备份集含唯一 ID、pg_dump 文件、清单与哈希字段。"""

        output_dir = tmp_path / "backups"

        backup_set = await create_backup(
            database_url=migrated_database_url,
            output_dir=output_dir,
            storage_root=str(tmp_path / "files"),
            daily_retention=7,
            weekly_retention=4,
        )

        # SPEC 27.2: 唯一 Backup ID
        assert backup_set.backup_id.startswith("backup-")
        assert len(backup_set.backup_id) > 10

        # SPEC 27.2: pg_dump 逻辑全量文件
        backup_dir = output_dir / backup_set.backup_id
        dump_path = backup_dir / backup_set.database_dump_file
        assert dump_path.exists()
        dump_content = dump_path.read_text(encoding="utf-8", errors="replace")
        assert len(dump_content) > 0

        # SPEC 27.2: READY 文件清单（数量/总大小/清单哈希）
        manifest_path = backup_dir / "manifest.json"
        assert manifest_path.exists()
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "manifest_sha256" in manifest_data
        assert len(manifest_data["manifest_sha256"]) == 64
        assert manifest_data["file_count"] == backup_set.file_count
        assert manifest_data["total_size_bytes"] == backup_set.total_size_bytes

        # manifest.json 可正确反序列化
        parsed = read_manifest(backup_dir)
        assert parsed.backup_id == backup_set.backup_id

    async def test_create_backup_db_unavailable(
        self,
        tmp_path: str,
    ) -> None:
        """SPEC 27.1: 数据库不可用时备份失败。"""

        bad_url = "postgresql+psycopg://nobody@127.0.0.1:1/nonexistent"

        with pytest.raises(BackupCreationError, match="数据库不可用"):
            await create_backup(
                database_url=bad_url,
                output_dir=Path(tmp_path) / "backups",
                storage_root=str(Path(tmp_path) / "files"),
                daily_retention=7,
                weekly_retention=4,
            )


@pytest.mark.g4
@pytest.mark.integration
class TestBackupVerify:
    """SPEC 27.3: 备份验证。"""

    async def test_verify_backup_success(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """验证报告含 Backup ID/RPO/RTO/检查结果字段。"""

        output_dir = tmp_path / "backups"

        # 先创建一个备份
        backup_set = await create_backup(
            database_url=migrated_database_url,
            output_dir=output_dir,
            storage_root=str(tmp_path / "files"),
            daily_retention=7,
            weekly_retention=4,
        )

        backup_dir = output_dir / backup_set.backup_id
        report_path = tmp_path / "report.json"

        # 验证备份
        report = await verify_backup(
            backup_dir=backup_dir,
            source_database_url=migrated_database_url,
            report_path=report_path,
        )

        # SPEC 27.3: 报告含 Backup ID/起止时间/实际 RPO/RTO/检查结果
        assert report.backup_id == backup_set.backup_id
        assert report.started_at <= report.finished_at
        assert report.actual_rpo_hours >= 0.0
        assert report.actual_rto_hours >= 0.0
        assert report.rpo_target_hours == 24.0
        assert report.rto_target_hours == 4.0
        assert report.migration_check.passed
        assert report.integrity_check.passed
        assert report.file_check.passed

        # SPEC 27.1: 实际 RPO/RTO 不超标
        assert report.actual_rpo_hours <= 24.0
        assert report.actual_rto_hours <= 4.0
        assert report.overall_passed
        assert report.failure_reason is None

        # 报告文件已写入
        assert report_path.exists()
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        assert report_data["backup_id"] == backup_set.backup_id
        assert "checks" in report_data
        assert report_data["overall_passed"] is True


@pytest.mark.g4
@pytest.mark.integration
class TestBackupWithReadyFiles:
    """SPEC 27.2: READY 文件纳入备份清单。"""

    async def test_backup_includes_ready_files(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """READY 文件出现在备份清单中。"""

        import hashlib

        from app.application.ports import SystemClock, UuidGenerator
        from app.infrastructure.db.engine import create_db_engine
        from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
        from app.modules.file.adapter import SqlAlchemyFileRepository
        from app.modules.file.models import FileMetadata, FileStatus

        storage_root = str(tmp_path / "files")
        output_dir = tmp_path / "backups"

        # 在数据库中创建一个 READY 文件元数据
        clock = SystemClock()
        id_gen = UuidGenerator()
        now = clock.now()
        file_id = id_gen.generate_id()
        storage_name = "test-backup-file.pdf"

        engine = create_db_engine(migrated_database_url)
        try:
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                repo = SqlAlchemyFileRepository(uow.session)
                # 创建物理文件
                files_dir = Path(storage_root) / "files"
                files_dir.mkdir(parents=True, exist_ok=True)
                file_path = files_dir / storage_name
                content = b"backup test content"
                file_path.write_bytes(content)
                sha = hashlib.sha256(content).hexdigest()

                metadata = FileMetadata(
                    id=file_id,
                    original_name="test.pdf",
                    storage_name=storage_name,
                    size_bytes=len(content),
                    content_type="application/pdf",
                    file_extension="pdf",
                    sha256=sha,
                    status=FileStatus.READY,
                    uploaded_by="test",
                    created_at=now,
                    updated_at=now,
                    deleting_entered_at=None,
                )
                await repo.add(metadata)
                await uow.commit()
        finally:
            await engine.dispose()

        # 创建备份
        backup_set = await create_backup(
            database_url=migrated_database_url,
            output_dir=output_dir,
            storage_root=storage_root,
            daily_retention=7,
            weekly_retention=4,
        )

        # 备份集包含 READY 文件
        assert backup_set.file_count >= 1
        entry = next(e for e in backup_set.files if e.storage_name == storage_name)
        assert entry.sha256 == sha

        # 物理文件已复制到备份集
        backup_dir = output_dir / backup_set.backup_id
        copied_file = backup_dir / "files" / storage_name
        assert copied_file.exists()
        assert copied_file.read_bytes() == content

        # 验证备份（文件一致性检查应通过）
        report = await verify_backup(
            backup_dir=backup_dir,
            source_database_url=migrated_database_url,
        )
        assert report.file_check.passed
        assert report.overall_passed
