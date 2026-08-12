"""数据字典 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 17.1 / 17.2）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case"。

路由组织（SPEC 9.1: 按模块分组）:
  字典类型管理 — ``/dict-types`` 前缀:
    POST   /dict-types                创建字典类型
    GET    /dict-types                查询字典类型列表
    GET    /dict-types/{typeId}      查询字典类型详情
    PUT    /dict-types/{typeId}      更新字典类型
    POST   /dict-types/{typeId}/enable   启用字典类型
    POST   /dict-types/{typeId}/disable  禁用字典类型
    DELETE /dict-types/{typeId}      删除字典类型（含删除保护）

  字典项管理 — ``/dict-types/{typeId}/items`` 前缀:
    POST   /dict-types/{typeId}/items            创建字典项
    GET    /dict-types/{typeId}/items            查询字典项列表
    GET    /dict-types/{typeId}/items/{itemId}  查询字典项详情
    PUT    /dict-types/{typeId}/items/{itemId}  更新字典项
    POST   /dict-types/{typeId}/items/{itemId}/enable   启用字典项
    POST   /dict-types/{typeId}/items/{itemId}/disable  禁用字典项
    DELETE /dict-types/{typeId}/items/{itemId}  删除字典项
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.application.context import UseCaseContext
from app.modules.auth.permission import require_permission
from app.modules.dict.schemas import (
    DictItemCreateRequest,
    DictItemResponse,
    DictItemUpdateRequest,
    DictTypeCreateRequest,
    DictTypeResponse,
    DictTypeUpdateRequest,
)
from app.modules.dict.use_case import DictUseCase

router = APIRouter(tags=["dict"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_dict_use_case(request: Request) -> DictUseCase:
    """构造 ``DictUseCase`` — 组合根装配（SPEC 5.2）."""

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter

    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    return DictUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=audit_factory,
    )


UseCaseDep = Annotated[DictUseCase, Depends(get_dict_use_case)]

DictReadCtx = Annotated[UseCaseContext, Depends(require_permission("dict:type:read"))]
DictWriteCtx = Annotated[UseCaseContext, Depends(require_permission("dict:type:write"))]


# ═══════════════════════════════════════════════════════════════════════════════
# 字典类型管理 — /dict-types
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/dict-types",
    response_model=DictTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建字典类型",
    operation_id="create_dict_type",
)
async def create_dict_type(
    response: Response,
    request_body: DictTypeCreateRequest,
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
) -> DictTypeResponse:
    """创建字典类型 — HTTP 201 + Location（SPEC 9.3 / 17.1）.

    字典编码全局唯一（SPEC 17.1: 字典编码保持稳定和唯一）。
    """

    result = await use_case.create_dict_type(ctx, request_body)
    response.headers["Location"] = f"/api/v1/dict-types/{result['id']}"
    return DictTypeResponse.model_validate(result)


@router.get(
    "/dict-types",
    response_model=list[DictTypeResponse],
    summary="查询字典类型列表",
    operation_id="list_dict_types",
)
async def list_dict_types(
    ctx: DictReadCtx,
    use_case: UseCaseDep,
    include_disabled: Annotated[
        bool,
        Query(
            alias="includeDisabled",
            description="是否包含禁用状态的字典类型（默认 true）",
        ),
    ] = True,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[DictTypeResponse]:
    """查询字典类型列表 — SPEC 17.1."""

    results, _ = await use_case.list_dict_types(
        ctx,
        include_disabled=include_disabled,
        offset=offset,
        limit=limit,
    )
    return [DictTypeResponse.model_validate(r) for r in results]


@router.get(
    "/dict-types/{typeId}",
    response_model=DictTypeResponse,
    summary="查询字典类型详情",
    operation_id="get_dict_type",
)
async def get_dict_type(
    ctx: DictReadCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
) -> DictTypeResponse:
    """查询字典类型详情 — SPEC 17.1."""

    result = await use_case.get_dict_type(ctx, type_id)
    return DictTypeResponse.model_validate(result)


@router.put(
    "/dict-types/{typeId}",
    response_model=DictTypeResponse,
    summary="更新字典类型",
    operation_id="update_dict_type",
)
async def update_dict_type(
    request_body: DictTypeUpdateRequest,
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
) -> DictTypeResponse:
    """更新字典类型 — SPEC 17.1.

    编码不可变更（稳定标识）。更新名称和描述。
    """

    result = await use_case.update_dict_type(ctx, type_id, request_body)
    return DictTypeResponse.model_validate(result)


@router.post(
    "/dict-types/{typeId}/enable",
    response_model=DictTypeResponse,
    summary="启用字典类型",
    operation_id="enable_dict_type",
)
async def enable_dict_type(
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
) -> DictTypeResponse:
    """启用字典类型 — SPEC 17.1."""

    result = await use_case.enable_dict_type(ctx, type_id)
    return DictTypeResponse.model_validate(result)


@router.post(
    "/dict-types/{typeId}/disable",
    response_model=DictTypeResponse,
    summary="禁用字典类型",
    operation_id="disable_dict_type",
)
async def disable_dict_type(
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
) -> DictTypeResponse:
    """禁用字典类型 — SPEC 17.1."""

    result = await use_case.disable_dict_type(ctx, type_id)
    return DictTypeResponse.model_validate(result)


@router.delete(
    "/dict-types/{typeId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除字典类型",
    operation_id="delete_dict_type",
)
async def delete_dict_type(
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
) -> None:
    """删除字典类型 — SPEC 17.1.

    SPEC 17.1: "已被业务引用的字典类型具有删除保护"。
    被引用登记 Port 标记为业务引用的字典类型不可删除。
    同时删除该类型下的全部字典项。
    """

    await use_case.delete_dict_type(ctx, type_id)


# ═══════════════════════════════════════════════════════════════════════════════
# 字典项管理 — /dict-types/{typeId}/items
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/dict-types/{typeId}/items",
    response_model=DictItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建字典项",
    operation_id="create_dict_item",
)
async def create_dict_item(
    response: Response,
    request_body: DictItemCreateRequest,
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
) -> DictItemResponse:
    """创建字典项 — HTTP 201 + Location（SPEC 9.3 / 17.2）.

    SPEC 17.2: 支持显示文本、稳定值、排序和扩展元数据。
    """

    result = await use_case.create_dict_item(ctx, type_id, request_body)
    response.headers["Location"] = f"/api/v1/dict-types/{type_id}/items/{result['id']}"
    return DictItemResponse.model_validate(result)


@router.get(
    "/dict-types/{typeId}/items",
    response_model=list[DictItemResponse],
    summary="查询字典项列表",
    operation_id="list_dict_items",
)
async def list_dict_items(
    ctx: DictReadCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
    include_disabled: Annotated[
        bool,
        Query(
            alias="includeDisabled",
            description="是否包含禁用状态的字典项（默认 true）",
        ),
    ] = True,
) -> list[DictItemResponse]:
    """查询字典项列表 — SPEC 17.2.

    返回结果按 ``sort_order`` 升序排列。
    """

    results = await use_case.list_dict_items(
        ctx,
        type_id,
        include_disabled=include_disabled,
    )
    return [DictItemResponse.model_validate(r) for r in results]


@router.get(
    "/dict-types/{typeId}/items/{itemId}",
    response_model=DictItemResponse,
    summary="查询字典项详情",
    operation_id="get_dict_item",
)
async def get_dict_item(
    ctx: DictReadCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
    item_id: Annotated[UUID, Path(alias="itemId", description="字典项 ID")],
) -> DictItemResponse:
    """查询字典项详情 — SPEC 17.2."""

    result = await use_case.get_dict_item(ctx, item_id)
    return DictItemResponse.model_validate(result)


@router.put(
    "/dict-types/{typeId}/items/{itemId}",
    response_model=DictItemResponse,
    summary="更新字典项",
    operation_id="update_dict_item",
)
async def update_dict_item(
    request_body: DictItemUpdateRequest,
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
    item_id: Annotated[UUID, Path(alias="itemId", description="字典项 ID")],
) -> DictItemResponse:
    """更新字典项 — SPEC 17.2.

    支持更新显示文本、稳定值、排序和扩展元数据。
    """

    result = await use_case.update_dict_item(ctx, item_id, request_body)
    return DictItemResponse.model_validate(result)


@router.post(
    "/dict-types/{typeId}/items/{itemId}/enable",
    response_model=DictItemResponse,
    summary="启用字典项",
    operation_id="enable_dict_item",
)
async def enable_dict_item(
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
    item_id: Annotated[UUID, Path(alias="itemId", description="字典项 ID")],
) -> DictItemResponse:
    """启用字典项 — SPEC 17.2."""

    result = await use_case.enable_dict_item(ctx, item_id)
    return DictItemResponse.model_validate(result)


@router.post(
    "/dict-types/{typeId}/items/{itemId}/disable",
    response_model=DictItemResponse,
    summary="禁用字典项",
    operation_id="disable_dict_item",
)
async def disable_dict_item(
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
    item_id: Annotated[UUID, Path(alias="itemId", description="字典项 ID")],
) -> DictItemResponse:
    """禁用字典项 — SPEC 17.2."""

    result = await use_case.disable_dict_item(ctx, item_id)
    return DictItemResponse.model_validate(result)


@router.delete(
    "/dict-types/{typeId}/items/{itemId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除字典项",
    operation_id="delete_dict_item",
)
async def delete_dict_item(
    ctx: DictWriteCtx,
    use_case: UseCaseDep,
    type_id: Annotated[UUID, Path(alias="typeId", description="字典类型 ID")],
    item_id: Annotated[UUID, Path(alias="itemId", description="字典项 ID")],
) -> None:
    """删除字典项 — SPEC 17.2."""

    await use_case.delete_dict_item(ctx, item_id)
