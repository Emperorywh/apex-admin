"""文件管理 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 19.2 / 19.4）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case"。

路由组织（SPEC 9.1: 按模块分组）:
  文件管理 — ``/files`` 前缀:
    POST   /files                 上传文件（multipart/form-data）
    GET    /files                 查询当前用户文件列表
    GET    /files/{file_id}       查询文件详情
    GET    /files/{file_id}/download  下载文件
    DELETE /files/{file_id}       删除文件（置 DELETING）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote
from uuid import UUID  # noqa: TC003

from fastapi import (
    APIRouter,
    Depends,
    File,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

from app.application.context import UseCaseContext
from app.modules.auth.permission import (
    ActorAuthorization,
    get_actor_authorization,
    require_permission,
)
from app.modules.file.schemas import FileMetadataResponse
from app.modules.file.use_case import FileUseCase

router = APIRouter(tags=["file"])

#: RFC 5987 编码所需的最大文件名展示长度。
_MAX_DISPOSITION_NAME = 200

#: 流式读取的块大小（64 KiB）。
_READ_CHUNK_SIZE = 64 * 1024


async def _upload_file_source(upload_file: UploadFile) -> AsyncIterator[bytes]:
    """将 ``UploadFile`` 适配为异步字节块迭代器 — SPEC 19.2.

    分块读取，禁止一次性读取整个文件到内存。
    """

    while True:
        chunk = await upload_file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def _get_allowed_types() -> dict[str, object]:
    """获取允许的文件类型白名单."""

    from app.modules.file.file_types import BUILTIN_FILE_TYPES

    return dict(BUILTIN_FILE_TYPES)


def get_file_use_case(request: Request) -> FileUseCase:
    """构造 ``FileUseCase`` — 组合根装配（SPEC 5.2）."""

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter
    from app.modules.file.storage import LocalFileStorageAdapter

    settings = request.app.state.settings
    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    storage = LocalFileStorageAdapter(settings.FILE_STORAGE_ROOT)

    return FileUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=audit_factory,
        storage=storage,
        allowed_types=_get_allowed_types(),  # type: ignore[arg-type]
        max_size_bytes=settings.FILE_MAX_SIZE_BYTES,
        max_upload_count=settings.FILE_MAX_UPLOAD_COUNT,
    )


UseCaseDep = Annotated[FileUseCase, Depends(get_file_use_case)]

FileReadCtx = Annotated[
    UseCaseContext,
    Depends(require_permission("file:manage:read")),
]
FileWriteCtx = Annotated[
    UseCaseContext,
    Depends(require_permission("file:manage:write")),
]


# ── 下载授权依赖 ─────────────────────────────────────────────────────────


def get_download_ctx(
    auth: Annotated[ActorAuthorization, Depends(get_actor_authorization)],
) -> tuple[UseCaseContext, bool]:
    """下载授权依赖 — 认证即可访问，is_admin 由权限决定.

    SPEC 19.4: "通用文件管理接口只允许上传者管理临时文件或拥有文件管理
    权限的管理员访问"。

    下载端点仅需认证（上传者可下载自己的文件）。
    ``is_admin`` 标志由是否拥有 ``file:manage:read`` 权限或超管身份决定。
    """

    is_admin = auth.is_super_admin or "file:manage:read" in auth.permissions
    return auth.ctx, is_admin


DownloadDep = Annotated[tuple[UseCaseContext, bool], Depends(get_download_ctx)]


# ═══════════════════════════════════════════════════════════════════════════════
# 上传
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/files",
    response_model=list[FileMetadataResponse],
    status_code=status.HTTP_201_CREATED,
    summary="上传文件",
    operation_id="upload_files",
)
async def upload_files(
    use_case: UseCaseDep,
    ctx: FileWriteCtx,
    files: Annotated[list[UploadFile], File(description="上传的文件列表")],
) -> list[FileMetadataResponse]:
    """上传文件 — multipart/form-data（SPEC 19.2 / 19.3）.

    SPEC 19.2:
      - 限制单文件大小。
      - 限制单次上传数量。
      - 分块流式写入，禁止一次性读取整个文件到内存。
      - 使用白名单校验允许的文件类型。

    每个文件独立处理，部分失败时已成功的文件仍然返回。
    """

    use_case.check_upload_count(len(files))

    results: list[dict[str, object]] = []
    for upload_file in files:
        result = await use_case.upload_file(
            ctx,
            original_name=upload_file.filename or "unnamed",
            content_type=upload_file.content_type or "application/octet-stream",
            source=_upload_file_source(upload_file),
        )
        results.append(result)

    return [FileMetadataResponse.model_validate(r) for r in results]


# ═══════════════════════════════════════════════════════════════════════════════
# 查询
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/files",
    response_model=list[FileMetadataResponse],
    summary="查询当前用户文件列表",
    operation_id="list_my_files",
)
async def list_my_files(
    use_case: UseCaseDep,
    ctx: FileReadCtx,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[FileMetadataResponse]:
    """查询当前用户的文件列表 — SPEC 19.4."""

    results, _ = await use_case.list_my_files(ctx, offset=offset, limit=limit)
    return [FileMetadataResponse.model_validate(r) for r in results]


@router.get(
    "/files/{file_id}",
    response_model=FileMetadataResponse,
    summary="查询文件详情",
    operation_id="get_file",
)
async def get_file(
    use_case: UseCaseDep,
    ctx: FileReadCtx,
    file_id: Annotated[UUID, Path(description="文件 ID")],
) -> FileMetadataResponse:
    """查询文件详情 — SPEC 19.1."""

    result = await use_case.get_file(ctx, file_id)
    return FileMetadataResponse.model_validate(result)


# ═══════════════════════════════════════════════════════════════════════════════
# 下载 — SPEC 19.2 / 19.3 / 19.4
# ═══════════════════════════════════════════════════════════════════════════════


def _build_content_disposition(filename: str) -> str:
    """构建 RFC 5987 编码的 Content-Disposition 响应头.

    SPEC 19.2: "响应头安全处理原始文件名"。
    使用 RFC 5987 ``filename*=UTF-8''<percent-encoded>`` 编码，
    确保非 ASCII 文件名安全传递。
    """

    # 截断过长的文件名
    safe_name = filename[:_MAX_DISPOSITION_NAME]
    encoded = quote(safe_name, safe="")
    return f"attachment; filename*=UTF-8''{encoded}"


@router.get(
    "/files/{file_id}/download",
    summary="下载文件",
    operation_id="download_file",
)
async def download_file(
    use_case: UseCaseDep,
    download_dep: DownloadDep,
    file_id: Annotated[UUID, Path(description="文件 ID")],
) -> StreamingResponse:
    """下载文件 — SPEC 19.2 / 19.3 / 19.4.

    SPEC 19.3: 仅 READY 文件可下载。
    SPEC 19.4: 上传者或拥有文件管理权限的管理员可下载。
    SPEC 19.2: 响应头安全处理原始文件名（RFC 5987）。
    SPEC 19.2: 下载接口防止未授权枚举文件（不区分 404 和 403）。
    """

    ctx, is_admin = download_dep
    metadata = await use_case.prepare_download(
        ctx,
        file_id,
        is_admin=is_admin,
    )
    file_path = use_case.get_download_path(metadata)

    # 打开读取流
    file_stream = use_case.open_download_stream(file_path)

    def _stream() -> Iterator[bytes]:
        try:
            while True:
                chunk = file_stream.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            file_stream.close()

    headers = {
        "Content-Disposition": _build_content_disposition(metadata.original_name),
    }

    return StreamingResponse(
        _stream(),
        media_type=metadata.content_type,
        headers=headers,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 删除 — SPEC 19.3 / 19.4
# ═══════════════════════════════════════════════════════════════════════════════


@router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除文件",
    operation_id="delete_file",
)
async def delete_file(
    use_case: UseCaseDep,
    download_dep: DownloadDep,
    file_id: Annotated[UUID, Path(description="文件 ID")],
) -> None:
    """删除文件 — SPEC 19.3 / 19.4.

    SPEC 19.3: "删除前必须在事务中确认没有活动业务引用并更新为 DELETING"。
    SPEC 19.3: 物理删除延迟至少 7 天（reconcile 在 TASK-026 执行）。

    SPEC 19.4: 上传者或拥有文件管理权限的管理员可删除。
    """

    ctx, is_admin = download_dep
    await use_case.delete_file(ctx, file_id, is_admin=is_admin)
