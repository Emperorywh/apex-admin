"""文件管理模块集成测试 — SPEC 19.1 / 19.2 / 19.3 / 19.4.

覆盖:
  - 上传全流程（流式写入、SHA-256、PENDING→READY）。
  - retain/release 幂等防重（唯一约束）。
  - 业务事务回滚时文件引用同时回滚。
  - 文件模块不反查业务表。
  - 上传/删除审计。

连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.context import UseCaseContext
from app.application.ports import SystemClock, UuidGenerator
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.audit.adapter import SqlAlchemyAuditRepository
from app.modules.file.errors import (
    FileForbiddenError,
    FileHasReferencesError,
    FileNotFoundError,
    FileNotReadyError,
)
from app.modules.file.file_types import BUILTIN_FILE_TYPES
from app.modules.file.models import FileMetadata, FileStatus
from app.modules.file.storage import LocalFileStorageAdapter
from app.modules.file.use_case import FileUseCase

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
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

_UPLOADER_ID = str(uuid4())
_OTHER_USER_ID = str(uuid4())


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


def _make_use_case(
    engine: AsyncEngine,
    storage_root: str,
    *,
    max_size: int = 52428800,
    max_count: int = 10,
) -> FileUseCase:
    """构造 FileUseCase。"""

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    storage = LocalFileStorageAdapter(storage_root)

    return FileUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=lambda session: SqlAlchemyAuditRepository(session),
        storage=storage,
        allowed_types=dict(BUILTIN_FILE_TYPES),
        max_size_bytes=max_size,
        max_upload_count=max_count,
    )


def _ctx(actor_id: str = _UPLOADER_ID) -> UseCaseContext:
    """构造测试上下文。"""

    return UseCaseContext(
        request_id="test-file-req",
        actor_id=actor_id,
    )


def _png_source() -> AsyncIterator[bytes]:
    """生成合法 PNG 文件内容（含 magic bytes）。"""

    async def _gen() -> AsyncIterator[bytes]:
        # PNG 文件头
        header = b"\x89PNG\r\n\x1a\n"
        # IHDR chunk (最小化)
        ihdr = (
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        )
        ihdr_crc = b"\xa9\x00\x00\x00"  # 简化的 CRC
        # IDAT chunk (最小化)
        idat = b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        idat_crc = b"\x00\x00\x00\x00"
        # IEND chunk
        iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"

        yield header + ihdr + ihdr_crc + idat + idat_crc + iend

    return _gen()


def _text_source(text: bytes = b"Hello World") -> AsyncIterator[bytes]:
    """生成文本文件内容。"""

    async def _gen() -> AsyncIterator[bytes]:
        yield text

    return _gen()


# ═══════════════════════════════════════════════════════════════════════════════
# 上传全流程 — SPEC 19.2 / 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestUploadFlow:
    """上传全流程集成测试 — SPEC 19.2 / 19.3."""

    def test_upload_creates_ready_file(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """上传全流程：流式写入、PENDING、rename、READY — SPEC 19.3."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="test.png",
                content_type="image/png",
                source=_png_source(),
            ),
        )

        assert result["status"] == "ready"
        assert result["original_name"] == "test.png"
        assert result["file_extension"] == "png"
        assert result["size_bytes"] > 0
        assert len(result["sha256"]) == 64  # SHA-256 hex
        assert result["uploaded_by"] == _UPLOADER_ID

        # 验证物理文件在正式目录中
        storage_name = result["storage_name"]
        final_file = tmp_path / "storage" / "files" / storage_name
        assert final_file.exists()
        # 临时目录中无残留
        temp_file = tmp_path / "storage" / "tmp" / storage_name
        assert not temp_file.exists()

    def test_upload_txt_file(self, migrated_database_url: str, tmp_path: Path) -> None:
        """文本文件上传 — 无 magic bytes 签名跳过内容特征校验."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="notes.txt",
                content_type="text/plain",
                source=_text_source(b"Hello file upload"),
            ),
        )

        assert result["status"] == "ready"
        assert result["file_extension"] == "txt"

    def test_upload_sha256_matches_content(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """上传后 SHA-256 与文件内容一致."""

        import hashlib

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        content = b"SHA-256 verification test"
        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="hash.txt",
                content_type="text/plain",
                source=_text_source(content),
            ),
        )

        expected = hashlib.sha256(content).hexdigest()
        assert result["sha256"] == expected

    def test_upload_writes_audit(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """上传写审计 — SPEC 19.2 / 18.2."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="audited.txt",
                content_type="text/plain",
                source=_text_source(b"audit test"),
            ),
        )

        # 查询审计表
        async def _check() -> int:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                stmt = text(
                    "SELECT COUNT(*) FROM audit_logs "
                    "WHERE module = 'file' AND action = 'file.upload'",
                )
                result = await uow.session.execute(stmt)
                return result.scalar() or 0

        count = asyncio.run(_check())
        assert count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 下载授权 — SPEC 19.3 / 19.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDownloadAuthorization:
    """下载授权集成测试 — SPEC 19.3 / 19.4."""

    def test_only_ready_files_downloadable(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """非 READY 文件不可下载 — SPEC 19.3."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        # 手动插入一个 PENDING 文件
        file_id = uuid4()
        now = datetime.now(UTC)

        async def _seed() -> None:
            from app.modules.file.adapter import SqlAlchemyFileRepository

            async with SqlAlchemyUnitOfWork(engine) as uow:
                repo = SqlAlchemyFileRepository(uow.session)
                await repo.add(
                    FileMetadata(
                        id=file_id,
                        original_name="pending.txt",
                        storage_name="abc.txt",
                        size_bytes=100,
                        content_type="text/plain",
                        file_extension="txt",
                        sha256="a" * 64,
                        status=FileStatus.PENDING,
                        uploaded_by=_UPLOADER_ID,
                        created_at=now,
                        updated_at=now,
                    ),
                )
                await uow.commit()

        asyncio.run(_seed())

        with pytest.raises(FileNotReadyError):
            asyncio.run(use_case.prepare_download(_ctx(), file_id))

    def test_cross_user_download_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """跨用户下载被拒绝 — SPEC 19.4."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        # 用户 A 上传
        result = asyncio.run(
            use_case.upload_file(
                _ctx(_UPLOADER_ID),
                original_name="secret.txt",
                content_type="text/plain",
                source=_text_source(b"secret content"),
            ),
        )
        file_id = result["id"]

        # 用户 B 尝试下载（非 admin）
        with pytest.raises(FileForbiddenError):
            asyncio.run(
                use_case.prepare_download(
                    _ctx(_OTHER_USER_ID),
                    file_id,
                    is_admin=False,
                ),
            )

    def test_admin_can_download_others(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """管理员可下载他人文件 — SPEC 19.4."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        # 用户 A 上传
        result = asyncio.run(
            use_case.upload_file(
                _ctx(_UPLOADER_ID),
                original_name="shared.txt",
                content_type="text/plain",
                source=_text_source(b"shared content"),
            ),
        )
        file_id = result["id"]

        # 管理员下载
        metadata = asyncio.run(
            use_case.prepare_download(
                _ctx(_OTHER_USER_ID),
                file_id,
                is_admin=True,
            ),
        )
        assert metadata.id == file_id

    def test_uploader_can_download_own(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """上传者可下载自己的文件 — SPEC 19.4."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(_UPLOADER_ID),
                original_name="own.txt",
                content_type="text/plain",
                source=_text_source(b"my file"),
            ),
        )

        metadata = asyncio.run(
            use_case.prepare_download(_ctx(_UPLOADER_ID), result["id"]),
        )
        assert metadata.original_name == "own.txt"

    def test_download_nonexistent_returns_not_found(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """下载不存在的文件返回 404 — SPEC 19.2: 不暴露可枚举信息."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        with pytest.raises(FileNotFoundError):
            asyncio.run(use_case.prepare_download(_ctx(), uuid4()))


# ═══════════════════════════════════════════════════════════════════════════════
# retain/release 幂等 — SPEC 19.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestRetainRelease:
    """retain/release 幂等防重 — SPEC 19.4."""

    def test_retain_idempotent(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """重复 retain 不产生重复记录（唯一约束保证幂等）."""

        from app.infrastructure.db.engine import create_db_engine
        from app.modules.file.adapter import SqlAlchemyFileReferenceAdapter

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        # 上传文件
        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="ref.txt",
                content_type="text/plain",
                source=_text_source(b"reference test"),
            ),
        )
        file_id = result["id"]

        # 重复 retain
        async def _retain_twice() -> int:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                adapter = SqlAlchemyFileReferenceAdapter(uow.session)
                await adapter.retain(
                    file_id,
                    "example",
                    "example_item",
                    "res-001",
                    created_at=datetime.now(UTC),
                )
                await adapter.retain(
                    file_id,
                    "example",
                    "example_item",
                    "res-001",  # 相同引用
                    created_at=datetime.now(UTC),
                )
                await uow.commit()

            # 查询引用数量
            async with SqlAlchemyUnitOfWork(engine) as uow:
                stmt = text("SELECT COUNT(*) FROM file_references")
                result = await uow.session.execute(stmt)
                return result.scalar() or 0

        count = asyncio.run(_retain_twice())
        assert count == 1  # 幂等：只一条记录

    def test_release_idempotent(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """重复 release 不产生错误（幂等）."""

        from app.infrastructure.db.engine import create_db_engine
        from app.modules.file.adapter import SqlAlchemyFileReferenceAdapter

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="release.txt",
                content_type="text/plain",
                source=_text_source(b"release test"),
            ),
        )
        file_id = result["id"]

        async def _release_twice() -> None:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                adapter = SqlAlchemyFileReferenceAdapter(uow.session)
                await adapter.release(file_id, "example", "example_item", "res-001")
                await adapter.release(file_id, "example", "example_item", "res-001")
                await uow.commit()

        asyncio.run(_release_twice())  # 不抛异常

    def test_retain_different_resources_distinct(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """不同资源 retain 产生不同记录."""

        from app.infrastructure.db.engine import create_db_engine
        from app.modules.file.adapter import SqlAlchemyFileReferenceAdapter

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="multi.txt",
                content_type="text/plain",
                source=_text_source(b"multi reference"),
            ),
        )
        file_id = result["id"]

        async def _retain_multi() -> int:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                adapter = SqlAlchemyFileReferenceAdapter(uow.session)
                await adapter.retain(
                    file_id,
                    "example",
                    "example_item",
                    "res-1",
                    created_at=datetime.now(UTC),
                )
                await adapter.retain(
                    file_id,
                    "example",
                    "example_item",
                    "res-2",
                    created_at=datetime.now(UTC),
                )
                await uow.commit()

            async with SqlAlchemyUnitOfWork(engine) as uow:
                stmt = text("SELECT COUNT(*) FROM file_references")
                result = await uow.session.execute(stmt)
                return result.scalar() or 0

        count = asyncio.run(_retain_multi())
        assert count == 2

    def test_business_rollback_rolls_back_references(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """业务事务回滚时文件引用同时回滚 — SPEC 19.4."""

        from app.infrastructure.db.engine import create_db_engine
        from app.modules.file.adapter import SqlAlchemyFileReferenceAdapter

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="rollback.txt",
                content_type="text/plain",
                source=_text_source(b"rollback test"),
            ),
        )
        file_id = result["id"]

        async def _retain_then_rollback() -> None:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                adapter = SqlAlchemyFileReferenceAdapter(uow.session)
                await adapter.retain(
                    file_id,
                    "example",
                    "example_item",
                    "res-1",
                    created_at=datetime.now(UTC),
                )
                # 模拟业务异常——不提交，直接回滚
                await uow.rollback()

        asyncio.run(_retain_then_rollback())

        # 引用应不存在
        async def _check() -> int:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                stmt = text("SELECT COUNT(*) FROM file_references")
                result = await uow.session.execute(stmt)
                return result.scalar() or 0

        count = asyncio.run(_check())
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 删除 — SPEC 19.3 / 19.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDeleteFlow:
    """删除集成测试 — SPEC 19.3 / 19.4."""

    def test_delete_transitions_to_deleting(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """删除将文件状态转为 DELETING — SPEC 19.3."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="delete.txt",
                content_type="text/plain",
                source=_text_source(b"delete me"),
            ),
        )
        file_id = result["id"]

        asyncio.run(use_case.delete_file(_ctx(), file_id))

        # 验证状态
        detail = asyncio.run(use_case.get_file(_ctx(), file_id))
        assert detail["status"] == "deleting"
        assert detail["deleting_entered_at"] is not None

    def test_delete_rejected_with_references(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """有活动引用的文件不可删除 — SPEC 19.4."""

        from app.infrastructure.db.engine import create_db_engine
        from app.modules.file.adapter import SqlAlchemyFileReferenceAdapter

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="referenced.txt",
                content_type="text/plain",
                source=_text_source(b"has reference"),
            ),
        )
        file_id = result["id"]

        # 添加引用
        async def _retain() -> None:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                adapter = SqlAlchemyFileReferenceAdapter(uow.session)
                await adapter.retain(
                    file_id,
                    "example",
                    "example_item",
                    "res-1",
                    created_at=datetime.now(UTC),
                )
                await uow.commit()

        asyncio.run(_retain())

        with pytest.raises(FileHasReferencesError):
            asyncio.run(use_case.delete_file(_ctx(), file_id))

    def test_delete_writes_audit(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """删除写审计 — SPEC 19.2 / 18.2."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="audit-delete.txt",
                content_type="text/plain",
                source=_text_source(b"audit delete"),
            ),
        )
        file_id = result["id"]

        asyncio.run(use_case.delete_file(_ctx(), file_id))

        async def _check() -> int:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                stmt = text(
                    "SELECT COUNT(*) FROM audit_logs "
                    "WHERE module = 'file' AND action = 'file.delete'",
                )
                result = await uow.session.execute(stmt)
                return result.scalar() or 0

        count = asyncio.run(_check())
        assert count == 1

    def test_cross_user_delete_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """跨用户删除被拒绝 — SPEC 19.4."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(_UPLOADER_ID),
                original_name="protected.txt",
                content_type="text/plain",
                source=_text_source(b"protected"),
            ),
        )

        with pytest.raises(FileForbiddenError):
            asyncio.run(
                use_case.delete_file(
                    _ctx(_OTHER_USER_ID),
                    result["id"],
                    is_admin=False,
                ),
            )

    def test_admin_can_delete_others(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """管理员可删除他人文件 — SPEC 19.4."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        result = asyncio.run(
            use_case.upload_file(
                _ctx(_UPLOADER_ID),
                original_name="admin-delete.txt",
                content_type="text/plain",
                source=_text_source(b"admin delete"),
            ),
        )

        asyncio.run(
            use_case.delete_file(
                _ctx(_OTHER_USER_ID),
                result["id"],
                is_admin=True,
            ),
        )

        detail = asyncio.run(use_case.get_file(_ctx(), result["id"]))
        assert detail["status"] == "deleting"
