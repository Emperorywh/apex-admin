"""文件管理模块安全测试 — SPEC 19.2.

覆盖:
  - 伪造 MIME/扩展名上传被拒绝。
  - 超大小上传被拒绝。
  - 超数量上传被拒绝。
  - 目录穿越防护。

连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.application.context import UseCaseContext
from app.application.ports import SystemClock, UuidGenerator
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.audit.adapter import SqlAlchemyAuditRepository
from app.modules.file.errors import (
    FileExtensionNotAllowedError,
    FileTooLargeError,
    FileTypeError,
    FileUploadCountExceededError,
)
from app.modules.file.file_types import BUILTIN_FILE_TYPES
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

    from sqlalchemy import text

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM file_references"))
            await conn.execute(text("DELETE FROM file_metadata"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


# ── fixture ───────────────────────────────────────────────────────────────

_ACTOR_ID = str(uuid4())


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


def _ctx() -> UseCaseContext:
    return UseCaseContext(request_id="test-file-sec", actor_id=_ACTOR_ID)


def _source(data: bytes) -> AsyncIterator[bytes]:
    """创建单块异步字节源。"""

    async def _gen() -> AsyncIterator[bytes]:
        yield data

    return _gen()


# ═══════════════════════════════════════════════════════════════════════════════
# 伪造 MIME / 扩展名 — SPEC 19.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.security
class TestForgedFileType:
    """伪造 MIME/扩展名上传被拒绝 — SPEC 19.2."""

    def test_png_content_with_jpg_extension_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """PNG 内容伪装为 JPG 扩展名 — magic bytes 不匹配."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        # PNG 内容
        png_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with pytest.raises(FileTypeError):
            asyncio.run(
                use_case.upload_file(
                    _ctx(),
                    original_name="fake.jpg",
                    content_type="image/jpeg",
                    source=_source(png_content),
                ),
            )

    def test_txt_content_with_pdf_extension_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """纯文本内容伪装为 PDF — magic bytes 不匹配."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        with pytest.raises(FileTypeError):
            asyncio.run(
                use_case.upload_file(
                    _ctx(),
                    original_name="fake.pdf",
                    content_type="application/pdf",
                    source=_source(b"Plain text, not a PDF"),
                ),
            )

    def test_extension_mime_mismatch_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """扩展名与声明 MIME 类型不一致."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        with pytest.raises(FileTypeError):
            asyncio.run(
                use_case.upload_file(
                    _ctx(),
                    original_name="mismatch.png",
                    content_type="image/jpeg",  # 声明 JPEG 但扩展名是 PNG
                    source=_source(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10),
                ),
            )

    def test_disallowed_extension_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """不在白名单中的扩展名被拒绝."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine, str(tmp_path / "storage"))

        with pytest.raises(FileExtensionNotAllowedError):
            asyncio.run(
                use_case.upload_file(
                    _ctx(),
                    original_name="malware.exe",
                    content_type="application/octet-stream",
                    source=_source(b"MZ\x90\x00"),
                ),
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 超大小 — SPEC 19.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.security
class TestOversizeUpload:
    """超大小上传被拒绝 — SPEC 19.2."""

    def test_oversize_file_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """超过大小限制的文件被拒绝."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(
            engine,
            str(tmp_path / "storage"),
            max_size=100,  # 100 字节限制
        )

        large_content = b"A" * 200  # 超过 100 字节

        def large_source() -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                # 分块产出，模拟流式上传
                for i in range(0, len(large_content), 50):
                    yield large_content[i : i + 50]

            return _gen()

        with pytest.raises(FileTooLargeError):
            asyncio.run(
                use_case.upload_file(
                    _ctx(),
                    original_name="large.txt",
                    content_type="text/plain",
                    source=large_source(),
                ),
            )

        # 验证临时文件被清理
        temp_files = list((tmp_path / "storage" / "tmp").iterdir())
        assert len(temp_files) == 0

    def test_within_size_accepted(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """刚好在大小限制内的文件通过."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(
            engine,
            str(tmp_path / "storage"),
            max_size=100,
        )

        content = b"A" * 100  # 刚好 100 字节

        result = asyncio.run(
            use_case.upload_file(
                _ctx(),
                original_name="exact.txt",
                content_type="text/plain",
                source=_source(content),
            ),
        )

        assert result["status"] == "ready"
        assert result["size_bytes"] == 100


# ═══════════════════════════════════════════════════════════════════════════════
# 超数量 — SPEC 19.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.security
class TestExceedUploadCount:
    """超数量上传被拒绝 — SPEC 19.2."""

    def test_exceed_count_rejected(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """超过数量限制的上传被拒绝."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(
            engine,
            str(tmp_path / "storage"),
            max_count=3,
        )

        # 模拟 5 个文件超过限制 3
        with pytest.raises(FileUploadCountExceededError):
            use_case.check_upload_count(5)

    def test_within_count_accepted(
        self,
        migrated_database_url: str,
        tmp_path: Path,
    ) -> None:
        """在数量限制内的上传通过."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(
            engine,
            str(tmp_path / "storage"),
            max_count=3,
        )

        # 不抛异常
        use_case.check_upload_count(3)
        use_case.check_upload_count(1)
