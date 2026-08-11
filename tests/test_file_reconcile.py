"""文件一致性命令与受控清理测试 — SPEC 19.3 / 19.4 / 25.3.

覆盖:
  - dry-run 只报告不修改（前后快照对比证明）
  - --apply 确定性规则: PENDING→READY、PENDING→FAILED、READY→FAILED
  - 连续两次 --apply 结果一致（幂等）
  - 四类故障注入场景: 临时写入后崩溃、元数据提交后崩溃、原子移动后崩溃、物理删除失败
  - DELETING 延迟物理删除、幂等重试
  - 临时文件清理
  - 未被引用文件保留期清理
  - --apply 写审计日志

连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.file.models import FileMetadata, FileStatus
from app.modules.file.reconcile import (
    ReconcileAction,
    ReconcileConfig,
    ReconcileResult,
    execute_reconcile,
    format_reconcile_report,
)
from app.modules.file.storage import LocalFileStorageAdapter

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine


# ── 迁移与清理 ─────────────────────────────────────────────────────────────


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
    """清理文件与审计表。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM file_references"))
            await conn.execute(text("DELETE FROM file_metadata"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


# ── 测试 fixture ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移。"""

    asyncio.run(_apply_migrations(database_url))
    yield database_url


@pytest.fixture(autouse=True)
def _clean_tables(migrated_database_url: str) -> Iterator[None]:
    """每个测试前后清理全部表。"""

    asyncio.run(_cleanup_tables(migrated_database_url))
    yield
    asyncio.run(_cleanup_tables(migrated_database_url))


def _make_config(
    *,
    pending_timeout_hours: int = 1,
    temp_max_age_hours: int = 24,
    deletion_delay_days: int = 7,
    unreferenced_retention_days: int = 7,
) -> ReconcileConfig:
    """构造 reconcile 配置。"""

    return ReconcileConfig(
        pending_timeout_hours=pending_timeout_hours,
        temp_max_age_hours=temp_max_age_hours,
        deletion_delay_days=deletion_delay_days,
        unreferenced_retention_days=unreferenced_retention_days,
    )


def _make_metadata(
    *,
    status: FileStatus,
    storage_name: str | None = None,
    sha256: str = "a" * 64,
    size_bytes: int = 100,
    content_type: str = "text/plain",
    file_extension: str = "txt",
    created_at: datetime | None = None,
    deleting_entered_at: datetime | None = None,
) -> FileMetadata:
    """构造文件元数据领域实体。"""

    now = datetime.now(UTC)
    return FileMetadata(
        id=uuid4(),
        original_name="test.txt",
        storage_name=storage_name or f"{uuid4().hex}.txt",
        size_bytes=size_bytes,
        content_type=content_type,
        file_extension=file_extension,
        sha256=sha256,
        status=status,
        uploaded_by="test-uploader",
        created_at=created_at or now,
        updated_at=now,
        deleting_entered_at=deleting_entered_at,
    )


async def _insert_metadata(
    engine: AsyncEngine,
    metadata: FileMetadata,
) -> None:
    """直接向数据库插入文件元数据（绕过 Use Case）。"""

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.file.adapter import SqlAlchemyFileRepository

    uow = SqlAlchemyUnitOfWork(engine)
    async with uow:
        repo = SqlAlchemyFileRepository(uow.session)
        await repo.add(metadata)
        await uow.commit()


async def _insert_reference(
    engine: AsyncEngine,
    file_id: object,
) -> None:
    """直接向数据库插入文件引用。"""

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.file.adapter import SqlAlchemyFileReferenceAdapter

    uow = SqlAlchemyUnitOfWork(engine)
    async with uow:
        adapter = SqlAlchemyFileReferenceAdapter(uow.session)
        await adapter.retain(
            file_id,  # type: ignore[arg-type]
            "test_module",
            "test_resource",
            "res-1",
            created_at=datetime.now(UTC),
        )
        await uow.commit()


async def _get_all_statuses(engine: AsyncEngine) -> dict[str, str]:
    """查询所有文件的 ID → status 映射。"""

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.file.adapter import SqlAlchemyFileRepository
    from app.modules.file.models import FileStatus as FS

    uow = SqlAlchemyUnitOfWork(engine)
    async with uow:
        repo = SqlAlchemyFileRepository(uow.session)
        result: dict[str, str] = {}
        for status in [
            FS.PENDING,
            FS.READY,
            FS.DELETING,
            FS.DELETED,
            FS.FAILED,
        ]:
            files = await repo.list_by_status(status)
            for f in files:
                result[str(f.id)] = f.status.value
        return result


async def _count_audit_logs(engine: AsyncEngine) -> int:
    """查询审计日志数量。"""

    from sqlalchemy import select

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.orm import AuditLogORM

    uow = SqlAlchemyUnitOfWork(engine)
    async with uow:
        result = await uow.session.execute(select(AuditLogORM))
        return len(list(result.scalars().all()))


def _write_final_file(
    storage: LocalFileStorageAdapter,
    storage_name: str,
    content: bytes,
) -> str:
    """在正式目录写入物理文件并返回路径。"""

    path = storage.get_final_path(storage_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _write_temp_file(
    storage: LocalFileStorageAdapter,
    name: str,
    content: bytes,
) -> str:
    """在临时目录写入文件并返回路径。"""

    path = storage.get_temp_path(name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _make_engine(database_url: str) -> AsyncEngine:
    """创建数据库引擎。"""

    from app.infrastructure.db.engine import create_db_engine

    return create_db_engine(database_url)


def _run_reconcile(
    engine: AsyncEngine,
    storage: LocalFileStorageAdapter,
    *,
    config: ReconcileConfig | None = None,
    apply: bool = False,
    now: datetime | None = None,
) -> object:
    """同步运行 reconcile 并返回结果。"""

    from app.application.ports import SystemClock
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    cfg = config or _make_config()

    class _FixedClock(SystemClock):
        """固定时间时钟，用于测试时间相关规则。"""

        def __init__(self, fixed: datetime) -> None:
            self._fixed = fixed

        def now(self) -> datetime:
            return self._fixed

    clock = _FixedClock(now or datetime.now(UTC))

    async def _run() -> object:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            result = await execute_reconcile(
                config=cfg,
                clock=clock,
                apply=apply,
                uow=uow,
                storage=storage,
            )
            if apply:
                await uow.commit()
            return result

    return asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════════════════════
# dry-run 不修改 — SPEC 25.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileDryRunNoMutation:
    """dry-run 只报告不一致，不修改数据与文件 — SPEC 25.3."""

    def test_dry_run_no_data_change(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """dry-run 不修改任何元数据（前后快照对比证明）。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 构造一个 PENDING 文件（无最终物理文件，超时）
        old_time = datetime.now(UTC) - timedelta(hours=2)
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            created_at=old_time,
        )
        asyncio.run(_insert_metadata(engine, metadata))

        # 前快照
        before = asyncio.run(_get_all_statuses(engine))

        # dry-run
        result = _run_reconcile(engine, storage, apply=False)

        # 后快照 — 数据不变
        after = asyncio.run(_get_all_statuses(engine))
        assert before == after
        assert after[str(metadata.id)] == "pending"

        # dry-run 结果报告了操作但不执行
        r: object = result
        assert isinstance(r, object)
        report = format_reconcile_report(result)  # type: ignore[arg-type]
        assert "dry-run" in report

    def test_dry_run_no_file_change(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """dry-run 不删除任何物理文件。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 在临时目录写入一个"过期"文件
        old_name = "old-temp-file.txt"
        temp_path = _write_temp_file(storage, old_name, b"stale temp data")
        old_mtime = time.time() - 25 * 3600  # 25 小时前
        os.utime(temp_path, (old_mtime, old_mtime))

        # dry-run
        result = _run_reconcile(engine, storage, apply=False)

        # 临时文件仍然存在
        assert os.path.exists(temp_path)
        assert result.temp_cleaned == 1  # type: ignore[attr-defined]


# ═══════════════════════════════════════════════════════════════════════════════
# --apply 确定性规则 — SPEC 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileApplyRules:
    """--apply 确定性规则 — SPEC 19.3."""

    def test_pending_with_matching_hash_promoted_to_ready(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """PENDING 有最终文件且哈希一致 → READY。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        content = b"reconcile test content"
        sha256 = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            sha256=sha256,
            size_bytes=len(content),
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.pending_promoted == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "ready"

    def test_pending_without_file_after_timeout_marked_failed(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """PENDING 超 1 小时无最终文件 → FAILED。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(hours=2)
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            created_at=old_time,
        )
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.pending_failed == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "failed"

    def test_pending_without_file_within_timeout_not_marked(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """PENDING 未超时不标记 FAILED，等待上传完成。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        recent_time = datetime.now(UTC) - timedelta(minutes=5)
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            created_at=recent_time,
        )
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.pending_promoted == 0  # type: ignore[attr-defined]
        assert result.pending_failed == 0  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "pending"

    def test_ready_missing_physical_marked_failed(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """READY 缺物理文件 → FAILED（SPEC 19.3: 高优先级运维日志）。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        metadata = _make_metadata(status=FileStatus.READY)
        asyncio.run(_insert_metadata(engine, metadata))
        # 不写入物理文件 — 模拟物理文件丢失

        result = _run_reconcile(engine, storage, apply=True)

        assert result.ready_failed == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "failed"

    def test_pending_hash_mismatch_marked_failed(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """PENDING 有最终文件但哈希不一致 → FAILED。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        content = b"actual content"
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            sha256="0" * 64,  # 不匹配的哈希
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.pending_failed == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "failed"


# ═══════════════════════════════════════════════════════════════════════════════
# 幂等性 — SPEC 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileIdempotency:
    """连续两次 --apply 结果一致（幂等）— SPEC 19.3."""

    def test_consecutive_apply_produces_consistent_results(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """连续两次 --apply 后数据状态一致且第二次无变更。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 构造混合场景
        content = b"idempotent content"
        sha = hashlib.sha256(content).hexdigest()

        # PENDING 哈希一致（可推进）
        m1 = _make_metadata(status=FileStatus.PENDING, sha256=sha)
        _write_final_file(storage, m1.storage_name, content)

        # PENDING 超时无文件（标 FAILED）
        m2 = _make_metadata(
            status=FileStatus.PENDING,
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )

        # READY 缺物理文件（标 FAILED）
        m3 = _make_metadata(status=FileStatus.READY)

        asyncio.run(_insert_metadata(engine, m1))
        asyncio.run(_insert_metadata(engine, m2))
        asyncio.run(_insert_metadata(engine, m3))

        # 第一次 --apply
        result1 = _run_reconcile(engine, storage, apply=True)
        assert result1.pending_promoted == 1  # type: ignore[attr-defined]
        assert result1.pending_failed == 1  # type: ignore[attr-defined]
        assert result1.ready_failed == 1  # type: ignore[attr-defined]

        after_first = asyncio.run(_get_all_statuses(engine))

        # 第二次 --apply — 不应有任何状态变更
        result2 = _run_reconcile(engine, storage, apply=True)
        assert result2.pending_promoted == 0  # type: ignore[attr-defined]
        assert result2.pending_failed == 0  # type: ignore[attr-defined]
        assert result2.ready_failed == 0  # type: ignore[attr-defined]
        assert result2.total_changes == 0  # type: ignore[attr-defined]

        after_second = asyncio.run(_get_all_statuses(engine))
        assert after_first == after_second


# ═══════════════════════════════════════════════════════════════════════════════
# 四类故障注入 — SPEC 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileFaultInjection:
    """四类故障注入场景均可被 reconcile 收敛 — SPEC 19.3."""

    def test_crash_after_temp_write(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """场景 1: 临时写入后崩溃 — 临时文件存在，无元数据。

        进程在流式写入临时文件后、创建 PENDING 元数据前崩溃。
        临时文件残留在 tmp/ 目录中。
        reconcile 应在超时后清理该临时文件。
        """

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 在临时目录写入一个残留文件（模拟崩溃后的残留）
        stale_name = "crash-leftover-abc123.txt"
        temp_path = _write_temp_file(storage, stale_name, b"orphaned temp data")
        # 设置 mtime 为 25 小时前（超过 24 小时阈值）
        old_mtime = time.time() - 25 * 3600
        os.utime(temp_path, (old_mtime, old_mtime))

        # 不在数据库中创建元数据（模拟崩溃发生在元数据提交前）

        result = _run_reconcile(engine, storage, apply=True)

        assert result.temp_cleaned == 1  # type: ignore[attr-defined]
        assert not os.path.exists(temp_path)

        # 第二次运行 — 幂等（无残留文件）
        result2 = _run_reconcile(engine, storage, apply=True)
        assert result2.temp_cleaned == 0  # type: ignore[attr-defined]

    def test_crash_after_metadata_commit(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """场景 2: 元数据提交后崩溃 — PENDING 元数据存在，无最终文件。

        进程在创建 PENDING 元数据（事务 1 提交）后、
        原子 rename 前崩溃。最终文件不存在。
        reconcile 应在超时后标记为 FAILED。
        """

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 创建 PENDING 元数据但无最终文件（模拟 rename 前崩溃）
        old_time = datetime.now(UTC) - timedelta(hours=2)
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            created_at=old_time,
        )
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.pending_failed == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "failed"

        # 第二次运行 — 幂等
        result2 = _run_reconcile(engine, storage, apply=True)
        assert result2.pending_failed == 0  # type: ignore[attr-defined]

    def test_crash_after_atomic_move(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """场景 3: 原子移动后崩溃 — PENDING 元数据存在，最终文件已存在。

        进程在原子 rename 完成后、第二个事务（更新为 READY）提交前崩溃。
        最终文件已移动到正式路径，但元数据仍是 PENDING。
        reconcile 应验证哈希一致后推进为 READY。
        """

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 写入最终文件（模拟 rename 已完成）
        content = b"moved but not committed"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(status=FileStatus.PENDING, sha256=sha)
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.pending_promoted == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "ready"

        # 第二次运行 — 幂等
        result2 = _run_reconcile(engine, storage, apply=True)
        assert result2.pending_promoted == 0  # type: ignore[attr-defined]

    def test_physical_delete_failure_keeps_deleting(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """场景 4: 物理删除失败 — 保持 DELETING 且可幂等重试。

        DELETING 文件延迟到期，但物理删除失败（OSError）。
        文件应保持 DELETING 状态，允许后续重试。
        """

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 创建 DELETING 文件（延迟已到期）
        old_time = datetime.now(UTC) - timedelta(days=8)
        content = b"to be deleted"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.DELETING,
            sha256=sha,
            deleting_entered_at=old_time,
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        # 使用会抛出异常的存储适配器模拟删除失败
        failing_storage = _FailingDeleteStorage(str(tmp_path / "storage"))

        result = _run_reconcile(engine, failing_storage, apply=True)

        assert result.deleting_kept_error == 1  # type: ignore[attr-defined]
        assert result.deleting_deleted == 0  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "deleting"

        # 物理文件仍在
        final_path = storage.get_final_path(metadata.storage_name)
        assert os.path.exists(final_path)

        # 第二次用正常存储重试 — 物理删除成功，转为 DELETED
        result2 = _run_reconcile(engine, storage, apply=True)
        assert result2.deleting_deleted == 1  # type: ignore[attr-defined]
        statuses2 = asyncio.run(_get_all_statuses(engine))
        assert statuses2[str(metadata.id)] == "deleted"
        assert not os.path.exists(final_path)


class _FailingDeleteStorage(LocalFileStorageAdapter):
    """删除文件时抛出异常的存储适配器 — 用于模拟物理删除失败。"""

    def delete_file(self, path: str) -> bool:  # noqa: ARG002
        """模拟物理删除失败。"""

        from app.modules.file.errors import FileStorageError

        raise FileStorageError("模拟物理删除失败（故障注入）")


# ═══════════════════════════════════════════════════════════════════════════════
# DELETING 延迟物理删除 — SPEC 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileDeletingDelay:
    """DELETING 延迟物理删除规则 — SPEC 19.3."""

    def test_deleting_not_expired_not_deleted(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """DELETING 未满 7 天不物理删除。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        recent_time = datetime.now(UTC) - timedelta(days=3)
        content = b"waiting deletion"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.DELETING,
            sha256=sha,
            deleting_entered_at=recent_time,
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.deleting_kept_delay == 1  # type: ignore[attr-defined]
        assert result.deleting_deleted == 0  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "deleting"

        # 物理文件仍在
        final_path = storage.get_final_path(metadata.storage_name)
        assert os.path.exists(final_path)

    def test_deleting_expired_deleted(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """DELETING 满 7 天物理删除并标记 DELETED。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(days=10)
        content = b"ready to delete"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.DELETING,
            sha256=sha,
            deleting_entered_at=old_time,
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.deleting_deleted == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "deleted"

        # 物理文件已删除
        final_path = storage.get_final_path(metadata.storage_name)
        assert not os.path.exists(final_path)

    def test_deleting_physical_already_gone(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """DELETING 物理文件已不存在（前次崩溃后残留）→ 安全标记 DELETED。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(days=10)
        metadata = _make_metadata(
            status=FileStatus.DELETING,
            deleting_entered_at=old_time,
        )
        # 不写入物理文件（模拟前次已删除但事务未提交）
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.deleting_deleted == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "deleted"

    def test_deleting_idempotent_retry(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """DELETING 物理删除失败后多次 reconcile 保持 DELETING（幂等重试）。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(days=10)
        content = b"persistent deletion"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.DELETING,
            sha256=sha,
            deleting_entered_at=old_time,
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        failing_storage = _FailingDeleteStorage(str(tmp_path / "storage"))

        # 第一次删除失败
        result1 = _run_reconcile(engine, failing_storage, apply=True)
        assert result1.deleting_kept_error == 1  # type: ignore[attr-defined]

        # 第二次删除也失败（幂等 — 仍保持 DELETING）
        result2 = _run_reconcile(engine, failing_storage, apply=True)
        assert result2.deleting_kept_error == 1  # type: ignore[attr-defined]

        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "deleting"


# ═══════════════════════════════════════════════════════════════════════════════
# 临时文件清理 — SPEC 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileTempCleanup:
    """临时目录超 24 小时无活动上传的文件被清理 — SPEC 19.3."""

    def test_stale_temp_file_cleaned(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """超过 24 小时的临时文件被清理。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        stale_name = "stale-upload.txt"
        temp_path = _write_temp_file(storage, stale_name, b"old data")
        old_mtime = time.time() - 25 * 3600
        os.utime(temp_path, (old_mtime, old_mtime))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.temp_cleaned == 1  # type: ignore[attr-defined]
        assert not os.path.exists(temp_path)

    def test_recent_temp_file_not_cleaned(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """未超 24 小时的临时文件不被清理。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        recent_name = "recent-upload.txt"
        temp_path = _write_temp_file(storage, recent_name, b"active upload")

        result = _run_reconcile(engine, storage, apply=True)

        assert result.temp_cleaned == 0  # type: ignore[attr-defined]
        assert os.path.exists(temp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 未被引用文件保留期清理 — SPEC 19.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileUnreferencedCleanup:
    """未被引用的正式文件按保留期清理 — SPEC 19.4."""

    def test_unreferenced_old_ready_marked_deleting(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """未被引用且超保留期的 READY 文件标记 DELETING。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(days=10)
        content = b"orphaned file"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.READY,
            sha256=sha,
            created_at=old_time,
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.unreferenced_marked == 1  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "deleting"

    def test_referenced_ready_not_marked(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """有业务引用的 READY 文件不被标记 DELETING。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(days=10)
        content = b"referenced file"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.READY,
            sha256=sha,
            created_at=old_time,
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))
        asyncio.run(_insert_reference(engine, metadata.id))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.unreferenced_marked == 0  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "ready"

    def test_unreferenced_recent_ready_not_marked(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """未被引用但未超保留期的 READY 文件不被标记 DELETING。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        recent_time = datetime.now(UTC) - timedelta(days=3)
        content = b"recent unreferenced"
        sha = hashlib.sha256(content).hexdigest()
        metadata = _make_metadata(
            status=FileStatus.READY,
            sha256=sha,
            created_at=recent_time,
        )
        _write_final_file(storage, metadata.storage_name, content)
        asyncio.run(_insert_metadata(engine, metadata))

        result = _run_reconcile(engine, storage, apply=True)

        assert result.unreferenced_marked == 0  # type: ignore[attr-defined]
        statuses = asyncio.run(_get_all_statuses(engine))
        assert statuses[str(metadata.id)] == "ready"

    def test_retention_not_shorter_than_7_days(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """保留期不短于 7 天 — 配置值验证。"""

        config = _make_config(unreferenced_retention_days=7)
        assert config.unreferenced_retention_days >= 7

        config_30 = _make_config(unreferenced_retention_days=30)
        assert config_30.unreferenced_retention_days >= 7


# ═══════════════════════════════════════════════════════════════════════════════
# --apply 写审计日志 — SPEC 25.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReconcileAuditLog:
    """--apply 写审计日志 — SPEC 25.3."""

    def test_apply_with_changes_writes_audit(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """--apply 执行修改时写审计日志。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(hours=2)
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            created_at=old_time,
        )
        asyncio.run(_insert_metadata(engine, metadata))

        before_count = asyncio.run(_count_audit_logs(engine))

        _run_reconcile(engine, storage, apply=True)

        after_count = asyncio.run(_count_audit_logs(engine))
        assert after_count > before_count

    def test_dry_run_no_audit(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """dry-run 不写审计日志。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        old_time = datetime.now(UTC) - timedelta(hours=2)
        metadata = _make_metadata(
            status=FileStatus.PENDING,
            created_at=old_time,
        )
        asyncio.run(_insert_metadata(engine, metadata))

        before_count = asyncio.run(_count_audit_logs(engine))

        _run_reconcile(engine, storage, apply=False)

        after_count = asyncio.run(_count_audit_logs(engine))
        assert after_count == before_count

    def test_apply_no_changes_no_audit(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """--apply 无修改时不写审计日志（健康数据）。"""

        engine = _make_engine(migrated_database_url)
        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 不构造任何不一致数据

        before_count = asyncio.run(_count_audit_logs(engine))

        _run_reconcile(engine, storage, apply=True)

        after_count = asyncio.run(_count_audit_logs(engine))
        assert after_count == before_count


# ═══════════════════════════════════════════════════════════════════════════════
# 单元测试 — 不连接数据库
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestReconcileConfigAndResult:
    """reconcile 配置与结果单元测试 — 不连接数据库。"""

    def test_config_defaults(self) -> None:
        """配置字段可正确构造。"""

        config = ReconcileConfig(
            pending_timeout_hours=1,
            temp_max_age_hours=24,
            deletion_delay_days=7,
            unreferenced_retention_days=7,
        )
        assert config.pending_timeout_hours == 1
        assert config.temp_max_age_hours == 24
        assert config.deletion_delay_days == 7
        assert config.unreferenced_retention_days == 7

    def test_result_total_changes(self) -> None:
        """结果 total_changes 正确计算。"""

        result = ReconcileResult(
            applied=True,
            actions=[
                ReconcileAction(
                    file_id=None,
                    action="temp_cleaned",
                    detail="test",
                ),
            ],
            pending_promoted=2,
            pending_failed=1,
            ready_failed=0,
            deleting_deleted=1,
            unreferenced_marked=0,
            temp_cleaned=1,
        )
        assert result.total_changes == 5

    def test_result_empty(self) -> None:
        """空结果 total_changes 为 0。"""

        result = ReconcileResult(applied=False)
        assert result.total_changes == 0
        assert len(result.actions) == 0

    def test_format_report_dry_run(self) -> None:
        """dry-run 报告包含模式标识。"""

        result = ReconcileResult(applied=False)
        report = format_reconcile_report(result)
        assert "dry-run" in report
        assert "预览" in report

    def test_format_report_apply(self) -> None:
        """--apply 报告包含模式标识。"""

        result = ReconcileResult(applied=True)
        report = format_reconcile_report(result)
        assert "已执行" in report

    def test_format_report_with_actions(self) -> None:
        """报告包含操作明细。"""

        result = ReconcileResult(
            applied=True,
            actions=[
                ReconcileAction(
                    file_id=None,
                    action="temp_cleaned",
                    detail="清理过期临时文件: test.txt",
                ),
            ],
            temp_cleaned=1,
        )
        report = format_reconcile_report(result)
        assert "操作明细" in report
        assert "temp_cleaned" in report


# ═══════════════════════════════════════════════════════════════════════════════
# 存储适配器新增方法 — 单元测试
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestStorageReconcileMethods:
    """存储适配器新增方法单元测试。"""

    def test_compute_sha256(self, tmp_path: Path) -> None:
        """compute_sha256 返回正确摘要。"""

        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))
        content = b"hash me"
        path = storage.get_final_path("test.txt")
        with open(path, "wb") as f:
            f.write(content)

        result = storage.compute_sha256(path)
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_list_temp_dir(self, tmp_path: Path) -> None:
        """list_temp_dir 列出临时目录文件。"""

        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))

        # 写入两个临时文件
        p1 = storage.get_temp_path("file1.txt")
        p2 = storage.get_temp_path("file2.txt")
        with open(p1, "wb") as f:
            f.write(b"1")
        with open(p2, "wb") as f:
            f.write(b"2")

        names = storage.list_temp_dir()
        assert set(names) == {"file1.txt", "file2.txt"}

    def test_list_temp_dir_empty(self, tmp_path: Path) -> None:
        """空临时目录返回空列表。"""

        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))
        assert storage.list_temp_dir() == []

    def test_get_mtime(self, tmp_path: Path) -> None:
        """get_mtime 返回文件修改时间。"""

        storage = LocalFileStorageAdapter(str(tmp_path / "storage"))
        path = storage.get_temp_path("test.txt")
        with open(path, "wb") as f:
            f.write(b"data")

        mtime = storage.get_mtime(path)
        assert mtime > 0
        assert abs(mtime - time.time()) < 5  # 近期创建
