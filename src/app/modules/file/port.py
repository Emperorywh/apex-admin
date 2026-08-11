"""文件管理 Repository Port、引用 Port 与存储 Port — SPEC 5.2 / 5.6 / 19.4.

SPEC 5.2: "Repository、Unit of Work、文件存储和外部服务 Port
由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port。

SPEC 19.4:
  - ``FileReferencePort`` 供业务模块通过 ``retain`` 和 ``release`` 管理引用，
    不直接写文件引用表。
  - 文件模块不反向查询业务模块数据表。

SPEC 19.1:
  - ``FileStoragePort`` 抽象本地文件存储操作，支持未来替换为对象存储（EXT）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from app.modules.file.models import FileMetadata


class FileRepository(ABC):
    """文件元数据 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型。
    返回值为领域实体（``FileMetadata``），不是 ORM 模型。
    """

    @abstractmethod
    async def add(self, metadata: FileMetadata) -> None:
        """添加新文件元数据到当前事务."""

    @abstractmethod
    async def get_by_id(self, file_id: UUID) -> FileMetadata | None:
        """按 ID 查询文件元数据，返回领域实体或 None。"""

    @abstractmethod
    async def save(self, metadata: FileMetadata) -> None:
        """保存文件元数据变更到当前事务."""

    @abstractmethod
    async def count_active_references(self, file_id: UUID) -> int:
        """查询文件的活动业务引用数量 — 删除保护用.

        SPEC 19.4: "删除前必须在事务中确认没有活动业务引用"。
        引用数量大于 0 时拒绝删除。
        """

    @abstractmethod
    async def list_by_uploader(
        self,
        uploaded_by: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[FileMetadata], int]:
        """查询指定上传者的文件列表（分页）."""


class FileReferencePort(ABC):
    """文件引用 Port — 跨模块公开（SPEC 5.2 / 5.5 / 19.4）.

    SPEC 5.5: "模块依赖只允许指向其他模块的公开 Application Port"。
    业务模块通过此 Port 执行 ``retain`` 和 ``release``，
    不直接写文件引用表。

    SPEC 19.4:
      - 文件引用必须包含业务模块编码、资源类型和资源 ID。
      - 具有防重复唯一约束——重复 retain 不产生重复记录。
      - 文件模块不反向查询业务模块数据表。

    SPEC 5.6: 此 Port 实现不自行提交或回滚事务。
    SPEC 5.7: 引用 retain/release 与业务数据在同一事务提交/回滚。
    """

    @abstractmethod
    async def retain(
        self,
        file_id: UUID,
        module_code: str,
        resource_type: str,
        resource_id: str,
        *,
        created_at: object,
    ) -> None:
        """登记文件业务引用 — 幂等.

        SPEC 19.4: "业务模块通过文件模块公开 Port 执行 retain"。
        重复 retain 同一 (file_id, module_code, resource_type, resource_id)
        不产生重复记录（复合唯一约束保证幂等）。

        参数:
            file_id:       被引用的文件 ID。
            module_code:   引用方业务模块编码。
            resource_type: 引用方资源类型。
            resource_id:   引用方资源标识。
            created_at:    引用创建时间（UTC datetime）。
        """

    @abstractmethod
    async def release(
        self,
        file_id: UUID,
        module_code: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        """释放文件业务引用 — 幂等.

        SPEC 19.4: "业务模块通过文件模块公开 Port 执行 release"。
        引用不存在时不产生错误（幂等）。

        参数:
            file_id:       被引用的文件 ID。
            module_code:   引用方业务模块编码。
            resource_type: 引用方资源类型。
            resource_id:   引用方资源标识。
        """


class FileReadPort(ABC):
    """文件读取 Port — 供业务模块下载附件时读取文件流（SPEC 19.4）.

    SPEC 19.4: "业务资源附件的下载必须先由业务模块校验资源访问权限，
    再调用文件读取 Port"。

    业务模块在完成自身授权校验后，通过此 Port 获取文件流。
    此 Port 只返回 READY 文件的流。
    """

    @abstractmethod
    async def open_read_stream(self, file_id: UUID) -> BinaryIO:
        """打开文件读取流.

        返回二进制读取流。调用方负责关闭流。
        文件不存在或非 READY 状态时抛出异常。
        """


class FileStoragePort(ABC):
    """本地文件存储 Port — SPEC 19.1 / 19.5.

    SPEC 19.1:
      - 文件名使用安全生成规则。
      - 防止目录穿越。
      - 临时目录与正式目录位于同一文件系统，确保原子 rename。
      - 存储目录不得位于 Web Root，应用不得跟随存储目录中的符号链接。

    SPEC 19.5: 业务模块依赖文件存储接口，而不是直接操作磁盘。
    此 Port 抽象文件存储操作，支持未来替换为对象存储（EXT）。
    """

    @abstractmethod
    def generate_storage_name(self, extension: str) -> str:
        """生成安全的存储文件名.

        SPEC 19.1: "文件名使用安全生成规则"。
        使用 UUID + 扩展名，不含用户输入，防止目录穿越。

        参数:
            extension: 文件扩展名（小写，不含点）。

        返回:
            安全的存储文件名（如 ``abc-def.txt``）。
        """

    @abstractmethod
    def get_temp_path(self, storage_name: str) -> str:
        """获取临时文件路径.

        返回临时目录中的文件绝对路径。
        """

    @abstractmethod
    def get_final_path(self, storage_name: str) -> str:
        """获取正式文件路径.

        返回正式目录中的文件绝对路径。
        """

    @abstractmethod
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

    @abstractmethod
    def atomic_rename(self, source: str, target: str) -> None:
        """原子 rename 文件 — SPEC 19.3.

        临时目录与正式目录位于同一文件系统，确保 ``os.rename`` 为原子操作。

        参数:
            source: 源文件路径。
            target: 目标文件路径。
        """

    @abstractmethod
    def delete_file(self, path: str) -> bool:
        """删除物理文件.

        返回是否删除成功（文件不存在时返回 False）。
        """

    @abstractmethod
    def exists(self, path: str) -> bool:
        """检查文件是否存在.

        不跟随符号链接（SPEC 19.1）。
        """

    @abstractmethod
    def open_read(self, path: str) -> BinaryIO:
        """打开文件读取流.

        返回二进制读取流。调用方负责关闭流。
        """

    @abstractmethod
    def cleanup_temp(self, path: str) -> None:
        """清理临时文件 — 尽力删除，不抛出异常."""
