"""文件元数据 Repository Adapter 与引用 Adapter — Infrastructure 层.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``FileRepository`` 和
``FileReferencePort``。Adapter 在内部将 ORM 模型与领域实体互转，
确保内层不感知 ORM 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.modules.file.models import FileMetadata, FileStatus
from app.modules.file.orm import FileMetadataORM, FileReferenceORM
from app.modules.file.port import FileReferencePort, FileRepository

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.file.port import FileStoragePort


class SqlAlchemyFileRepository(FileRepository):
    """SQLAlchemy 异步文件元数据 Repository Adapter."""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def add(self, metadata: FileMetadata) -> None:
        """添加新文件元数据到当前事务."""

        orm = _metadata_to_orm(metadata)
        self._session.add(orm)
        await self._session.flush()

    async def get_by_id(self, file_id: UUID) -> FileMetadata | None:
        """按 ID 查询文件元数据."""

        stmt = select(FileMetadataORM).where(FileMetadataORM.id == file_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_metadata(orm) if orm else None

    async def save(self, metadata: FileMetadata) -> None:
        """保存文件元数据变更."""

        stmt = select(FileMetadataORM).where(FileMetadataORM.id == metadata.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.file.errors import FileNotFoundError

            raise FileNotFoundError(str(metadata.id))

        orm.original_name = metadata.original_name
        orm.storage_name = metadata.storage_name
        orm.size_bytes = metadata.size_bytes
        orm.content_type = metadata.content_type
        orm.file_extension = metadata.file_extension
        orm.sha256 = metadata.sha256
        orm.status = metadata.status.value
        orm.uploaded_by = metadata.uploaded_by
        orm.updated_at = metadata.updated_at
        orm.deleting_entered_at = metadata.deleting_entered_at
        await self._session.flush()

    async def count_active_references(self, file_id: UUID) -> int:
        """查询文件的活动业务引用数量."""

        stmt = (
            select(func.count())
            .select_from(FileReferenceORM)
            .where(FileReferenceORM.file_id == file_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def list_by_uploader(
        self,
        uploaded_by: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[FileMetadata], int]:
        """查询指定上传者的文件列表（分页）."""

        base = (
            select(FileMetadataORM)
            .where(FileMetadataORM.uploaded_by == uploaded_by)
            .order_by(FileMetadataORM.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(base)
        files = [_orm_to_metadata(orm) for orm in result.scalars().all()]

        count_stmt = (
            select(func.count())
            .select_from(FileMetadataORM)
            .where(FileMetadataORM.uploaded_by == uploaded_by)
        )
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar() or 0

        return files, total


class SqlAlchemyFileReferenceAdapter(FileReferencePort):
    """SQLAlchemy 异步文件引用 Adapter.

    SPEC 19.4: 业务模块通过此 Adapter 执行 retain 和 release。
    retain 使用 PostgreSQL ``ON CONFLICT DO NOTHING`` 实现幂等。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化引用 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def retain(
        self,
        file_id: UUID,
        module_code: str,
        resource_type: str,
        resource_id: str,
        *,
        created_at: object,
    ) -> None:
        """登记文件业务引用 — 幂等（ON CONFLICT DO NOTHING）."""

        from uuid import uuid4

        stmt = pg_insert(FileReferenceORM).values(
            id=uuid4(),
            file_id=file_id,
            module_code=module_code,
            resource_type=resource_type,
            resource_id=resource_id,
            created_at=created_at,
        )
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_file_references_fid_mod_type_res",
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def release(
        self,
        file_id: UUID,
        module_code: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        """释放文件业务引用 — 幂等."""

        stmt = delete(FileReferenceORM).where(
            FileReferenceORM.file_id == file_id,
            FileReferenceORM.module_code == module_code,
            FileReferenceORM.resource_type == resource_type,
            FileReferenceORM.resource_id == resource_id,
        )
        await self._session.execute(stmt)
        await self._session.flush()


class SqlAlchemyFileReadService:
    """文件读取服务 — 实现 ``FileReadPort`` 供业务模块下载附件（SPEC 19.4）.

    SPEC 19.4: "业务资源附件的下载必须先由业务模块校验资源访问权限，
    再调用文件读取 Port"。

    此服务在业务模块完成自身授权校验后被调用，只返回 READY 文件的流。
    文件模块不反向查询业务模块数据表（SPEC 19.4）。
    """

    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        storage: FileStoragePort,
    ) -> None:
        """初始化读取服务.

        参数:
            uow_factory: UoW 工厂（用于读取文件元数据）。
            storage:     文件存储 Port。
        """

        self._uow_factory = uow_factory
        self._storage = storage

    async def open_read_stream(self, file_id: UUID) -> BinaryIO:
        """打开文件读取流 — SPEC 19.4.

        验证文件存在且为 READY 状态，返回二进制读取流。
        调用方负责关闭流。
        """

        from app.modules.file.errors import (
            FileNotFoundError,
            FileNotReadyError,
            FileStorageError,
        )
        from app.modules.file.models import FileStatus

        async with self._uow_factory() as uow:
            repo = SqlAlchemyFileRepository(uow.session)
            metadata = await repo.get_by_id(file_id)
            if metadata is None:
                raise FileNotFoundError(str(file_id))
            if metadata.status != FileStatus.READY:
                raise FileNotReadyError(str(file_id))
            storage_name = metadata.storage_name
            original_name = metadata.original_name

        final_path = self._storage.get_final_path(storage_name)
        if not self._storage.exists(final_path):
            raise FileStorageError(
                f"READY 文件物理缺失: {original_name} ({file_id})",
            )
        return self._storage.open_read(final_path)


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _orm_to_metadata(orm: FileMetadataORM) -> FileMetadata:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return FileMetadata(
        id=orm.id,
        original_name=orm.original_name,
        storage_name=orm.storage_name,
        size_bytes=orm.size_bytes,
        content_type=orm.content_type,
        file_extension=orm.file_extension,
        sha256=orm.sha256,
        status=FileStatus(orm.status),
        uploaded_by=orm.uploaded_by,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        deleting_entered_at=orm.deleting_entered_at,
    )


def _metadata_to_orm(metadata: FileMetadata) -> FileMetadataORM:
    """领域实体 → ORM 模型转换."""

    return FileMetadataORM(
        id=metadata.id,
        original_name=metadata.original_name,
        storage_name=metadata.storage_name,
        size_bytes=metadata.size_bytes,
        content_type=metadata.content_type,
        file_extension=metadata.file_extension,
        sha256=metadata.sha256,
        status=metadata.status.value,
        uploaded_by=metadata.uploaded_by,
        created_at=metadata.created_at,
        updated_at=metadata.updated_at,
        deleting_entered_at=metadata.deleting_entered_at,
    )
