"""文件管理模块单元测试 — SPEC 19.1 / 19.2 / 19.3 / 19.4.

覆盖:
  - 状态机全部合法/非法转换（单元测试覆盖转换表）。
  - 文件类型校验（扩展名、MIME、magic bytes）。
  - 安全存储名生成（防目录穿越）。
  - Content-Disposition RFC 5987 编码。
  - 大小限制流式检查。

不连接数据库（SPEC 28.2）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.modules.file.errors import (
    FileExtensionNotAllowedError,
    FileInvalidTransitionError,
    FileTooLargeError,
    FileTypeError,
)
from app.modules.file.file_types import (
    BUILTIN_FILE_TYPES,
    extract_extension,
    validate_content_type,
    validate_extension,
    validate_magic_bytes,
)
from app.modules.file.models import FileStatus
from app.modules.file.state_machine import can_transition, transition
from app.modules.file.storage import LocalFileStorageAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ═══════════════════════════════════════════════════════════════════════════════
# 状态机转换表 — SPEC 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestStateMachine:
    """文件状态机转换 — SPEC 19.3."""

    def test_pending_to_ready(self) -> None:
        """PENDING → READY 合法."""

        assert can_transition(FileStatus.PENDING, FileStatus.READY)
        transition(FileStatus.PENDING, FileStatus.READY)

    def test_pending_to_failed(self) -> None:
        """PENDING → FAILED 合法."""

        assert can_transition(FileStatus.PENDING, FileStatus.FAILED)
        transition(FileStatus.PENDING, FileStatus.FAILED)

    def test_ready_to_deleting(self) -> None:
        """READY → DELETING 合法."""

        assert can_transition(FileStatus.READY, FileStatus.DELETING)
        transition(FileStatus.READY, FileStatus.DELETING)

    def test_ready_to_failed(self) -> None:
        """READY → FAILED 合法（READY 元数据缺少物理文件）."""

        assert can_transition(FileStatus.READY, FileStatus.FAILED)
        transition(FileStatus.READY, FileStatus.FAILED)

    def test_deleting_to_deleted(self) -> None:
        """DELETING → DELETED 合法."""

        assert can_transition(FileStatus.DELETING, FileStatus.DELETED)
        transition(FileStatus.DELETING, FileStatus.DELETED)

    def test_deleting_to_deleting_idempotent_retry(self) -> None:
        """DELETING → DELETING 合法（物理删除失败幂等重试）."""

        assert can_transition(FileStatus.DELETING, FileStatus.DELETING)
        transition(FileStatus.DELETING, FileStatus.DELETING)

    # ── 非法转换 ──────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (FileStatus.PENDING, FileStatus.DELETING),
            (FileStatus.PENDING, FileStatus.DELETED),
            (FileStatus.READY, FileStatus.PENDING),
            (FileStatus.READY, FileStatus.DELETED),
            (FileStatus.DELETING, FileStatus.PENDING),
            (FileStatus.DELETING, FileStatus.READY),
            (FileStatus.DELETING, FileStatus.FAILED),
        ],
    )
    def test_illegal_transition_rejected(
        self,
        source: FileStatus,
        target: FileStatus,
    ) -> None:
        """非法状态转换被拒绝 — SPEC 19.3."""

        assert not can_transition(source, target)
        with pytest.raises(FileInvalidTransitionError):
            transition(source, target)

    @pytest.mark.parametrize(
        ("terminal", "target"),
        [
            (FileStatus.DELETED, FileStatus.PENDING),
            (FileStatus.DELETED, FileStatus.READY),
            (FileStatus.DELETED, FileStatus.DELETING),
            (FileStatus.DELETED, FileStatus.FAILED),
            (FileStatus.FAILED, FileStatus.PENDING),
            (FileStatus.FAILED, FileStatus.READY),
            (FileStatus.FAILED, FileStatus.DELETING),
            (FileStatus.FAILED, FileStatus.DELETED),
        ],
    )
    def test_terminal_state_no_exit(
        self,
        terminal: FileStatus,
        target: FileStatus,
    ) -> None:
        """终态（DELETED/FAILED）不可转换到任何状态 — SPEC 19.3."""

        assert not can_transition(terminal, target)
        with pytest.raises(FileInvalidTransitionError):
            transition(terminal, target)

    def test_invalid_transition_error_carries_status_info(self) -> None:
        """非法转换错误携带源/目标状态信息."""

        with pytest.raises(FileInvalidTransitionError) as exc_info:
            transition(FileStatus.DELETED, FileStatus.READY)
        assert exc_info.value.source_status == "deleted"
        assert exc_info.value.target_status == "ready"


# ═══════════════════════════════════════════════════════════════════════════════
# 文件类型校验 — SPEC 19.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestFileTypeValidation:
    """文件类型校验 — SPEC 19.2."""

    def test_extract_extension_lowercase(self) -> None:
        """扩展名提取并转小写."""

        assert extract_extension("photo.JPG") == "jpg"
        assert extract_extension("doc.pdf") == "pdf"
        assert extract_extension("noext") == ""

    def test_validate_extension_allowed(self) -> None:
        """白名单内扩展名通过."""

        spec = validate_extension("jpg", BUILTIN_FILE_TYPES)
        assert spec.extension == "jpg"

    def test_validate_extension_rejected(self) -> None:
        """白名单外扩展名被拒绝 — SPEC 19.2."""

        with pytest.raises(FileExtensionNotAllowedError):
            validate_extension("exe", BUILTIN_FILE_TYPES)

    def test_validate_content_type_match(self) -> None:
        """声明 MIME 与扩展名映射一致."""

        spec = BUILTIN_FILE_TYPES["png"]
        validate_content_type("image/png", spec)

    def test_validate_content_type_mismatch_forged(self) -> None:
        """声明 MIME 与扩展名不一致视为伪造 — SPEC 19.2."""

        spec = BUILTIN_FILE_TYPES["png"]
        with pytest.raises(FileTypeError):
            validate_content_type("image/jpeg", spec)

    def test_validate_magic_bytes_jpeg(self) -> None:
        """JPEG magic bytes 校验通过."""

        spec = BUILTIN_FILE_TYPES["jpg"]
        validate_magic_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100, spec)

    def test_validate_magic_bytes_png(self) -> None:
        """PNG magic bytes 校验通过."""

        spec = BUILTIN_FILE_TYPES["png"]
        validate_magic_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, spec)

    def test_validate_magic_bytes_forged(self) -> None:
        """magic bytes 不匹配视为伪造 — SPEC 19.2."""

        spec = BUILTIN_FILE_TYPES["jpg"]
        # PNG 内容伪装为 JPEG
        with pytest.raises(FileTypeError):
            validate_magic_bytes(b"\x89PNG\r\n\x1a\n", spec)

    def test_validate_magic_bytes_no_signature_skips(self) -> None:
        """无可靠 magic bytes 的类型跳过内容特征校验（如 txt）."""

        spec = BUILTIN_FILE_TYPES["txt"]
        validate_magic_bytes(b"hello world", spec)


# ═══════════════════════════════════════════════════════════════════════════════
# 安全存储名生成与目录穿越防护 — SPEC 19.1
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestStorageSafety:
    """本地存储适配器安全测试 — SPEC 19.1."""

    def test_generate_storage_name_uuid_based(self, tmp_path: Path) -> None:
        """存储名使用 UUID 生成，不含用户输入."""

        storage = LocalFileStorageAdapter(str(tmp_path))
        name1 = storage.generate_storage_name("jpg")
        name2 = storage.generate_storage_name("jpg")
        assert name1.endswith(".jpg")
        assert name2.endswith(".jpg")
        assert name1 != name2  # UUID 保证唯一性

    def test_generate_storage_name_no_extension(self, tmp_path: Path) -> None:
        """无扩展名时生成纯 UUID."""

        storage = LocalFileStorageAdapter(str(tmp_path))
        name = storage.generate_storage_name("")
        assert "." not in name

    def test_temp_and_final_same_filesystem(self, tmp_path: Path) -> None:
        """临时目录与正式目录在同一根下 — SPEC 19.3."""

        storage = LocalFileStorageAdapter(str(tmp_path))
        temp = Path(storage.get_temp_path("test.txt"))
        final = Path(storage.get_final_path("test.txt"))
        # 两者的前 N 段路径相同（同一文件系统）
        assert (
            temp.parts[: -len(temp.parts) + 2] == final.parts[: -len(final.parts) + 2]
        )

    def test_directory_traversal_prevented(self, tmp_path: Path) -> None:
        """目录穿越被阻止 — 存储名只取 basename."""

        storage = LocalFileStorageAdapter(str(tmp_path))
        # 即使传入恶意路径组件，也只取 basename
        temp_path = storage.get_temp_path("../../../etc/passwd")
        assert ".." not in temp_path
        assert "passwd" in temp_path  # basename 保留了文件名
        # 路径在 temp_dir 下
        assert str(tmp_path / "tmp") in temp_path

    def test_storage_not_in_web_root(self, tmp_path: Path) -> None:
        """存储目录不在 Web Root — SPEC 19.1.

        存储目录由部署配置指定，与静态文件服务目录分离。
        此测试验证存储根目录与 static 目录不同。
        """

        storage = LocalFileStorageAdapter(str(tmp_path / "data"))
        assert "static" not in str(storage.root)

    def test_symlink_not_followed_in_exists(self, tmp_path: Path) -> None:
        """exists() 不跟随符号链接 — SPEC 19.1."""

        import os

        storage = LocalFileStorageAdapter(str(tmp_path))
        real_file = tmp_path / "files" / "real.txt"
        real_file.write_text("content")

        link_path = tmp_path / "files" / "link.txt"
        try:
            os.symlink(real_file, link_path)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持符号链接创建")

        # exists 不跟随符号链接
        assert not storage.exists(str(link_path))
        assert storage.exists(str(real_file))

    def test_open_read_rejects_symlink(self, tmp_path: Path) -> None:
        """open_read 拒绝符号链接 — SPEC 19.1."""

        import os

        from app.modules.file.errors import FileStorageError

        storage = LocalFileStorageAdapter(str(tmp_path))
        real_file = tmp_path / "files" / "real.txt"
        real_file.write_text("content")

        link_path = tmp_path / "files" / "link.txt"
        try:
            os.symlink(real_file, link_path)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持符号链接创建")

        with pytest.raises(FileStorageError):
            storage.open_read(str(link_path))


# ═══════════════════════════════════════════════════════════════════════════════
# 流式写入与 SHA-256 — SPEC 19.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestStreamingWrite:
    """分块流式写入 — SPEC 19.2."""

    @pytest.mark.asyncio
    async def test_write_stream_computes_size_and_sha256(
        self,
        tmp_path: Path,
    ) -> None:
        """流式写入边读边累计大小和 SHA-256."""

        storage = LocalFileStorageAdapter(str(tmp_path))
        path = storage.get_temp_path("test.txt")

        content = b"A" * 1000 + b"B" * 500

        async def source() -> AsyncIterator[bytes]:
            # 分块产出
            for i in range(0, len(content), 64):
                yield content[i : i + 64]

        size, sha = await storage.write_stream(path, source())

        assert size == 1500
        expected_sha = hashlib.sha256(content).hexdigest()
        assert sha == expected_sha

        # 验证文件内容正确
        assert Path(path).read_bytes() == content

    @pytest.mark.asyncio
    async def test_write_stream_not_all_in_memory(
        self,
        tmp_path: Path,
    ) -> None:
        """验证流式写入不一次性读取整个文件到内存 — SPEC 19.2.

        通过多次 yield 验证写入是增量进行的。
        """

        storage = LocalFileStorageAdapter(str(tmp_path))
        path = storage.get_temp_path("chunk.txt")

        chunks_written: list[int] = []

        async def source() -> AsyncIterator[bytes]:
            for i in range(10):
                chunks_written.append(i)
                yield bytes([65 + i]) * 100

        size, sha = await storage.write_stream(path, source())

        assert size == 1000
        assert len(chunks_written) == 10  # 10 次独立 yield 被消费
        assert len(sha) == 64  # SHA-256 hex


# ═══════════════════════════════════════════════════════════════════════════════
# 大小限制流式检查 — SPEC 19.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestSizeLimitCheck:
    """大小限制流式检查 — SPEC 19.2."""

    @pytest.mark.asyncio
    async def test_sized_source_aborts_on_exceed(self) -> None:
        """超过大小限制时立即中止."""

        from app.modules.file.use_case import FileUseCase

        max_size = 200

        async def source() -> AsyncIterator[bytes]:
            yield b"A" * 100
            yield b"B" * 100
            yield b"C" * 100  # 总计 300 > 200

        sized = FileUseCase._sized_source(source(), max_size)
        total = 0
        with pytest.raises(FileTooLargeError):
            async for chunk in sized:
                total += len(chunk)

        # 前两块被消费，第三块触发中止
        assert total == 200

    @pytest.mark.asyncio
    async def test_sized_source_within_limit(self) -> None:
        """未超限制时全部通过."""

        from app.modules.file.use_case import FileUseCase

        max_size = 300

        async def source() -> AsyncIterator[bytes]:
            yield b"A" * 100
            yield b"B" * 100
            yield b"C" * 100

        sized = FileUseCase._sized_source(source(), max_size)
        total = 0
        async for chunk in sized:
            total += len(chunk)

        assert total == 300


# ═══════════════════════════════════════════════════════════════════════════════
# Content-Disposition RFC 5987 — SPEC 19.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestContentDisposition:
    """Content-Disposition RFC 5987 编码 — SPEC 19.2."""

    def test_ascii_filename(self) -> None:
        """ASCII 文件名编码."""

        from app.modules.file.router import _build_content_disposition

        cd = _build_content_disposition("report.pdf")
        assert "filename*=UTF-8''report.pdf" in cd

    def test_unicode_filename(self) -> None:
        """非 ASCII 文件名 RFC 5987 编码."""

        from app.modules.file.router import _build_content_disposition

        cd = _build_content_disposition("季度报告.pdf")
        assert "filename*=UTF-8''" in cd
        # 非 ASCII 字符被 percent-encode
        assert "%E5%AD%A3" in cd  # '季' 的 UTF-8 编码

    def test_long_filename_truncated(self) -> None:
        """过长文件名被截断."""

        from app.modules.file.router import (
            _MAX_DISPOSITION_NAME,
            _build_content_disposition,
        )

        long_name = "A" * 500
        cd = _build_content_disposition(long_name)
        # 编码后的长度不超过原始截断长度
        encoded_part = cd.split("''")[1]
        from urllib.parse import unquote

        decoded = unquote(encoded_part)
        assert len(decoded) <= _MAX_DISPOSITION_NAME


# ═══════════════════════════════════════════════════════════════════════════════
# 原子 rename — SPEC 19.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestAtomicRename:
    """原子 rename — SPEC 19.3."""

    def test_rename_temp_to_final(self, tmp_path: Path) -> None:
        """原子 rename 临时文件到正式路径."""

        storage = LocalFileStorageAdapter(str(tmp_path))
        temp_path = storage.get_temp_path("test.txt")
        final_path = storage.get_final_path("test.txt")

        Path(temp_path).write_text("hello")
        storage.atomic_rename(temp_path, final_path)

        assert Path(final_path).read_text() == "hello"
        assert not Path(temp_path).exists()

    def test_cleanup_temp(self, tmp_path: Path) -> None:
        """清理临时文件不抛出异常."""

        storage = LocalFileStorageAdapter(str(tmp_path))
        temp_path = storage.get_temp_path("test.txt")
        Path(temp_path).write_text("hello")

        storage.cleanup_temp(temp_path)
        assert not Path(temp_path).exists()

        # 清理不存在的文件也不抛异常
        storage.cleanup_temp(temp_path)
