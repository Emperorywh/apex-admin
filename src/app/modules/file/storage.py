"""本地文件存储适配器 — Infrastructure 层 — SPEC 19.1 / 19.2 / 19.3.

SPEC 19.1:
  - 文件名使用安全生成规则（UUID 基，不含用户输入）。
  - 防止目录穿越（生成的存储名不受用户控制）。
  - 临时目录与正式目录位于同一文件系统，确保原子 rename。
  - 存储目录不得位于 Web Root。
  - 应用不跟随存储目录中的符号链接。

SPEC 19.2:
  - 分块流式写入，边读边累计大小和 SHA-256，禁止一次性读取整个文件到内存。

SPEC 19.3:
  - 原子 rename 将临时文件移动到最终路径。

此适配器实现 ``FileStoragePort``，封装所有文件系统操作。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO
from uuid import uuid4

from app.modules.file.errors import FileStorageError
from app.modules.file.port import FileStoragePort

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


#: 流式写入的块大小（64 KiB）— SPEC 19.2: 禁止一次性读取整个文件到内存。
_CHUNK_SIZE = 64 * 1024

#: magic bytes 校验读取的最大头部长度。
_MAGIC_BYTES_READ_SIZE = 512


class LocalFileStorageAdapter(FileStoragePort):
    """本地文件系统存储适配器 — SPEC 19.1 / 19.2 / 19.3.

    目录结构::

        {storage_root}/
        ├── tmp/     临时目录（上传时流式写入）
        └── files/   正式目录（原子 rename 后的最终位置）

    两个子目录在同一文件系统下，保证 ``os.rename`` 为原子操作（SPEC 19.3）。

    安全措施:
      - 存储名使用 UUID + 扩展名，不含用户输入。
      - 目录穿越被阻止（用户无法控制路径组件）。
      - ``os.path.islink`` 检查拒绝跟随符号链接。
      - 存储目录不得位于 Web Root（由配置保证）。
    """

    def __init__(self, storage_root: str) -> None:
        """初始化存储适配器，创建目录结构.

        参数:
            storage_root: 存储根目录绝对路径。
        """

        root = Path(storage_root).resolve()
        self._root = root
        self._temp_dir = root / "tmp"
        self._files_dir = root / "files"

        # 创建目录结构（幂等）
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._files_dir.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """存储根目录."""

        return self._root

    def generate_storage_name(self, extension: str) -> str:
        """生成安全的存储文件名 — SPEC 19.1.

        使用 UUID + 扩展名，不含用户输入。
        """

        safe_ext = extension.lstrip(".").lower()
        if safe_ext:
            return f"{uuid4().hex}.{safe_ext}"
        return uuid4().hex

    def get_temp_path(self, storage_name: str) -> str:
        """获取临时文件路径."""

        return str(self._temp_dir / self._safe_basename(storage_name))

    def get_final_path(self, storage_name: str) -> str:
        """获取正式文件路径."""

        return str(self._files_dir / self._safe_basename(storage_name))

    async def write_stream(
        self,
        path: str,
        source: AsyncIterator[bytes],
    ) -> tuple[int, str]:
        """分块流式写入文件 — SPEC 19.2.

        边读边累计大小和 SHA-256，禁止一次性读取整个文件到内存。

        参数:
            path:   目标文件路径。
            source: 异步字节块迭代器。

        返回:
            (文件大小, SHA-256 十六进制摘要) 元组。
        """

        sha256 = hashlib.sha256()
        total_size = 0

        try:
            with open(path, "wb") as f:
                async for chunk in source:
                    f.write(chunk)
                    sha256.update(chunk)
                    total_size += len(chunk)
        except OSError as exc:
            raise FileStorageError(
                f"文件写入失败: {path} — {exc}",
            ) from exc

        return total_size, sha256.hexdigest()

    def atomic_rename(self, source: str, target: str) -> None:
        """原子 rename 文件 — SPEC 19.3.

        临时目录与正式目录位于同一文件系统，确保 ``os.rename`` 为原子操作。
        """

        try:
            # 确保目标目录存在
            target_dir = os.path.dirname(target)
            os.makedirs(target_dir, exist_ok=True)
            os.rename(source, target)
        except OSError as exc:
            raise FileStorageError(
                f"原子 rename 失败: {source} → {target} — {exc}",
            ) from exc

    def delete_file(self, path: str) -> bool:
        """删除物理文件，返回是否删除成功（文件不存在时返回 False）。"""

        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise FileStorageError(
                f"文件删除失败: {path} — {exc}",
            ) from exc

    def exists(self, path: str) -> bool:
        """检查文件是否存在 — 不跟随符号链接（SPEC 19.1）。"""

        # os.path.lexists 检查链接本身是否存在（不跟随），
        # os.path.islink 判断是否为符号链接。
        if os.path.islink(path):
            return False
        return os.path.isfile(path)

    def open_read(self, path: str) -> BinaryIO:
        """打开文件读取流."""

        # 拒绝符号链接 — SPEC 19.1: "应用不得跟随存储目录中的符号链接"
        if os.path.islink(path):
            raise FileStorageError(
                f"拒绝跟随符号链接: {path}",
            )
        if not os.path.isfile(path):
            raise FileStorageError(
                f"文件不存在: {path}",
            )
        return open(path, "rb")

    def cleanup_temp(self, path: str) -> None:
        """清理临时文件 — 尽力删除，不抛出异常。"""

        import contextlib

        with contextlib.suppress(FileNotFoundError, OSError):
            os.unlink(path)

    def read_head_bytes(self, path: str) -> bytes:
        """读取文件头部字节用于 magic bytes 校验."""

        if os.path.islink(path):
            raise FileStorageError(
                f"拒绝跟随符号链接: {path}",
            )
        with open(path, "rb") as f:
            return f.read(_MAGIC_BYTES_READ_SIZE)

    # ── 内部辅助 ──────────────────────────────────────────────────────

    @staticmethod
    def _safe_basename(name: str) -> str:
        """提取安全 basename，防止目录穿越.

        SPEC 19.1: "防止目录穿越"。
        只取文件名部分，丢弃任何路径分隔符或 ``..`` 组件。
        由于存储名由服务器生成（UUID + 扩展名），这里作为防御性二次校验。
        """

        # os.path.basename 提取最后一段，丢弃路径前缀
        return os.path.basename(name)
