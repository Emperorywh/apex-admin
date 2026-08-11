"""文件管理 Use Case — Application 层应用服务.

SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

SPEC 19.1: 本地文件存储（安全命名、防目录穿越、临时/正式同文件系统）。
SPEC 19.2: 上传与下载（分块流式、大小/数量/类型白名单、授权下载）。
SPEC 19.3: 文件状态机（PENDING → READY → DELETING → DELETED）。
SPEC 19.4: 业务引用与授权边界（retain/release Port）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app.modules.audit.models import AuditEntry
from app.modules.file.errors import (
    FileForbiddenError,
    FileHasReferencesError,
    FileNotFoundError,
    FileNotReadyError,
    FileTooLargeError,
    FileUploadCountExceededError,
)
from app.modules.file.file_types import (
    FileTypeSpec,
    extract_extension,
    validate_content_type,
    validate_extension,
    validate_magic_bytes,
)
from app.modules.file.models import FileMetadata, FileStatus
from app.modules.file.state_machine import transition

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.context import UseCaseContext
    from app.application.ports import Clock, IdGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.port import AuditPort
    from app.modules.file.adapter import SqlAlchemyFileRepository
    from app.modules.file.port import FileStoragePort


class FileUseCase:
    """文件管理 Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

    构造参数:
        uow_factory:       UoW 工厂。
        clock:             时钟 Port。
        id_generator:      标识生成器 Port。
        audit_factory:     审计 Port 工厂。
        storage:           文件存储 Port。
        allowed_types:     允许的文件类型白名单。
        max_size_bytes:    单文件最大字节数。
        max_upload_count:  单次上传最大文件数。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        audit_factory: Callable[[AsyncSession], AuditPort],
        storage: FileStoragePort,
        allowed_types: dict[str, FileTypeSpec],
        max_size_bytes: int,
        max_upload_count: int,
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._audit_factory = audit_factory
        self._storage = storage
        self._allowed_types = allowed_types
        self._max_size_bytes = max_size_bytes
        self._max_upload_count = max_upload_count

    def _create_repo(self, session: AsyncSession) -> SqlAlchemyFileRepository:
        """从 session 构造 Repository Adapter — SPEC 5.6."""

        from app.modules.file.adapter import SqlAlchemyFileRepository

        return SqlAlchemyFileRepository(session)

    def _create_audit(self, session: AsyncSession) -> AuditPort:
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return self._audit_factory(session)

    def _make_audit_entry(
        self,
        ctx: UseCaseContext,
        *,
        action: str,
        resource_id: str,
        resource_display_name: str | None,
    ) -> AuditEntry:
        """构造操作审计条目 — SPEC 18.2 / 5.7."""

        return AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="file",
            action=action,
            resource_type="file",
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result="success",
            request_id=ctx.request_id or None,
            diff=None,
            occurred_at=self._clock.now(),
        )

    # ════════════════════════════════════════════════════════════════════════
    # 上传 — SPEC 19.2 / 19.3
    # ════════════════════════════════════════════════════════════════════════

    def check_upload_count(self, count: int) -> None:
        """检查上传文件数量是否超过限制 — SPEC 19.2.

        参数:
            count: 本次上传的文件数量。

        抛出:
            FileUploadCountExceededError: 数量超过限制。
        """

        if count > self._max_upload_count:
            raise FileUploadCountExceededError(
                f"单次上传文件数量 {count} 超过限制 {self._max_upload_count}",
            )

    async def upload_file(
        self,
        ctx: UseCaseContext,
        *,
        original_name: str,
        content_type: str,
        source: AsyncIterator[bytes],
    ) -> dict[str, object]:
        """上传单个文件 — SPEC 19.2 / 19.3.

        全流程:
          1. 校验扩展名白名单。
          2. 校验声明 MIME 类型与扩展名映射一致。
          3. 生成安全存储名。
          4. 分块流式写入临时文件，边读边累计大小和 SHA-256（禁止一次性读取）。
          5. 校验大小限制（流式检查，超限立即中止）。
          6. 校验 magic bytes。
          7. 创建 PENDING 元数据（事务 1 提交）。
          8. 原子 rename 临时文件到正式路径。
          9. 在新事务中更新为 READY 并写审计（事务 2 提交）。

        失败时尽力清理临时文件。

        参数:
            ctx:           用例上下文。
            original_name: 原始文件名（用户输入）。
            content_type:  声明的 MIME 类型。
            source:        异步字节块迭代器。

        返回:
            文件元数据响应字典。
        """

        # 1. 校验扩展名
        extension = extract_extension(original_name)
        type_spec = validate_extension(extension, self._allowed_types)

        # 2. 校验声明 MIME 类型与扩展名一致
        validate_content_type(content_type, type_spec)

        # 3. 生成安全存储名
        storage_name = self._storage.generate_storage_name(extension)
        temp_path = self._storage.get_temp_path(storage_name)
        final_path = self._storage.get_final_path(storage_name)

        file_id = self._id_generator.generate_id()
        now = self._clock.now()

        try:
            # 4-5. 分块流式写入 + 大小检查
            sized_source = self._sized_source(source, self._max_size_bytes)
            size_bytes, sha256 = await self._storage.write_stream(
                temp_path,
                sized_source,
            )

            # 6. 校验 magic bytes
            head_bytes = self._storage.read_head_bytes(temp_path)  # type: ignore[attr-defined]
            validate_magic_bytes(head_bytes, type_spec)

            # 7. 创建 PENDING 元数据（事务 1）
            metadata = FileMetadata(
                id=file_id,
                original_name=original_name,
                storage_name=storage_name,
                size_bytes=size_bytes,
                content_type=content_type,
                file_extension=extension,
                sha256=sha256,
                status=FileStatus.PENDING,
                uploaded_by=ctx.actor_id,
                created_at=now,
                updated_at=now,
                deleting_entered_at=None,
            )
            async with self._uow_factory() as uow:
                repo = self._create_repo(uow.session)
                await repo.add(metadata)
                await uow.commit()

            # 8. 原子 rename
            self._storage.atomic_rename(temp_path, final_path)

            # 9. 更新为 READY + 审计（事务 2）
            ready_time = self._clock.now()
            async with self._uow_factory() as uow:
                repo = self._create_repo(uow.session)
                audit = self._create_audit(uow.session)

                existing = await repo.get_by_id(file_id)
                if existing is None:
                    # 不应发生——刚创建的 PENDING 元数据
                    raise FileNotFoundError(str(file_id))

                transition(existing.status, FileStatus.READY)
                ready_metadata = replace(
                    existing,
                    status=FileStatus.READY,
                    updated_at=ready_time,
                )
                await repo.save(ready_metadata)

                await audit.record_audit(
                    self._make_audit_entry(
                        ctx,
                        action="file.upload",
                        resource_id=str(file_id),
                        resource_display_name=original_name,
                    ),
                )
                await uow.commit()
                return _metadata_to_response(ready_metadata)

        except Exception:
            # 失败时尽力清理临时文件 — SPEC 19.3
            self._storage.cleanup_temp(temp_path)
            raise

    @staticmethod
    async def _sized_source(
        source: AsyncIterator[bytes],
        max_size: int,
    ) -> AsyncIterator[bytes]:
        """包装异步字节迭代器，流式检查大小.

        SPEC 19.2: "上传必须分块流式写入并在读取过程中累计大小和 SHA-256，
        禁止一次性读取整个文件到内存"。

        超过大小限制时立即中止迭代并抛出异常。
        """

        total = 0
        async for chunk in source:
            total += len(chunk)
            if total > max_size:
                raise FileTooLargeError(
                    f"文件大小 {total} 超过限制 {max_size} 字节",
                )
            yield chunk

    # ════════════════════════════════════════════════════════════════════════
    # 下载 — SPEC 19.2 / 19.3 / 19.4
    # ════════════════════════════════════════════════════════════════════════

    async def prepare_download(
        self,
        ctx: UseCaseContext,
        file_id: UUID,
        *,
        is_admin: bool = False,
    ) -> FileMetadata:
        """准备文件下载 — 校验 READY 状态和授权.

        SPEC 19.3: "API 只允许下载 READY 文件"。
        SPEC 19.4: "通用文件管理接口只允许上传者管理临时文件或拥有文件管理
        权限的管理员访问"。

        参数:
            ctx:      用例上下文。
            file_id:  文件 ID。
            is_admin: 操作者是否具有文件管理权限（由 Router 判定）。

        返回:
            文件元数据领域实体（Router 据此构建下载响应）。

        抛出:
            FileNotFoundError:     文件不存在。
            FileNotReadyError:     文件不是 READY 状态。
            FileForbiddenError:    跨用户下载被拒绝。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            metadata = await repo.get_by_id(file_id)
            if metadata is None:
                raise FileNotFoundError(str(file_id))

            # SPEC 19.3: 仅 READY 文件可下载
            if metadata.status != FileStatus.READY:
                raise FileNotReadyError(str(file_id))

            # SPEC 19.4: 上传者或文件管理权限管理员
            if metadata.uploaded_by != ctx.actor_id and not is_admin:
                raise FileForbiddenError(
                    "无权下载他人上传的文件",
                )

            return metadata

    def get_download_path(self, metadata: FileMetadata) -> str:
        """获取下载文件路径."""

        return self._storage.get_final_path(metadata.storage_name)

    def open_download_stream(self, path: str):  # type: ignore[no-untyped-def]
        """打开下载文件读取流 — 供 Router 构建 StreamingResponse."""

        return self._storage.open_read(path)

    # ════════════════════════════════════════════════════════════════════════
    # 删除 — SPEC 19.3 / 19.4
    # ════════════════════════════════════════════════════════════════════════

    async def delete_file(
        self,
        ctx: UseCaseContext,
        file_id: UUID,
        *,
        is_admin: bool = False,
    ) -> None:
        """删除文件 — SPEC 19.3 / 19.4.

        SPEC 19.3: "删除前必须在事务中确认没有活动业务引用并更新为 DELETING，
        并记录进入 DELETING 的时间"。
        SPEC 19.3: "DELETING 文件的物理删除至少延迟 7 天"——物理删除由
        reconcile 命令在 TASK-026 执行。

        SPEC 19.4: "通用文件管理接口只允许上传者管理临时文件或拥有文件管理
        权限的管理员访问"。

        参数:
            ctx:      用例上下文。
            file_id:  文件 ID。
            is_admin: 操作者是否具有文件管理权限。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            metadata = await repo.get_by_id(file_id)
            if metadata is None:
                raise FileNotFoundError(str(file_id))

            # SPEC 19.4: 上传者或文件管理权限管理员
            if metadata.uploaded_by != ctx.actor_id and not is_admin:
                raise FileForbiddenError(
                    "无权删除他人上传的文件",
                )

            # SPEC 19.4: 删除前确认无活动业务引用
            ref_count = await repo.count_active_references(file_id)
            if ref_count > 0:
                raise FileHasReferencesError(
                    f"文件 {file_id} 被 {ref_count} 个业务资源引用，不可删除",
                )

            # SPEC 19.3: 状态转换 READY → DELETING
            transition(metadata.status, FileStatus.DELETING)
            deleting_metadata = replace(
                metadata,
                status=FileStatus.DELETING,
                updated_at=now,
                deleting_entered_at=now,
            )
            await repo.save(deleting_metadata)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="file.delete",
                    resource_id=str(file_id),
                    resource_display_name=metadata.original_name,
                ),
            )
            await uow.commit()

    # ════════════════════════════════════════════════════════════════════════
    # 查询
    # ════════════════════════════════════════════════════════════════════════

    async def get_file(
        self,
        ctx: UseCaseContext,
        file_id: UUID,
    ) -> dict[str, object]:
        """查询文件元数据详情."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            metadata = await repo.get_by_id(file_id)
            if metadata is None:
                raise FileNotFoundError(str(file_id))
            return _metadata_to_response(metadata)

    async def list_my_files(
        self,
        ctx: UseCaseContext,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, object]], int]:
        """查询当前上传者的文件列表."""

        assert ctx.actor_id is not None

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            files, total = await repo.list_by_uploader(
                ctx.actor_id,
                offset=offset,
                limit=limit,
            )
            return [_metadata_to_response(f) for f in files], total


# ── 响应转换辅助 ──────────────────────────────────────────────────────────


def _metadata_to_response(metadata: FileMetadata) -> dict[str, object]:
    """文件元数据领域实体 → 响应字典."""

    return {
        "id": metadata.id,
        "original_name": metadata.original_name,
        "storage_name": metadata.storage_name,
        "size_bytes": metadata.size_bytes,
        "content_type": metadata.content_type,
        "file_extension": metadata.file_extension,
        "sha256": metadata.sha256,
        "status": metadata.status.value,
        "uploaded_by": metadata.uploaded_by,
        "created_at": metadata.created_at,
        "updated_at": metadata.updated_at,
        "deleting_entered_at": metadata.deleting_entered_at,
    }
