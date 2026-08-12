"""示例 Router — API 层（SPEC 5.2 / 9.1 / 9.2）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository 或提交接口"。

Router 通过 FastAPI 依赖注入获得 ``ExampleItemUseCase``，
将 HTTP 请求转换为 Use Case 调用，将 Use Case 返回值转换为 HTTP 响应。
Router 不接触 UoW、Repository 或 AsyncSession。

路由命名与 HTTP 方法语义一致（SPEC 9.1）。
创建成功返回 HTTP 201（SPEC 9.3）。
无响应体的删除成功返回 HTTP 204（SPEC 9.3）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.responses import Response

from app.application.context import UseCaseContext
from app.core.api.pagination import (
    PageParams,
    PageResponse,
    SortField,
    sort_dependency,
)
from app.core.context.dependencies import create_use_case_context
from app.modules.example.schemas import (
    ExampleItemCreateRequest,
    ExampleItemResponse,
    ExampleItemUpdateRequest,
)
from app.modules.example.use_case import ExampleItemUseCase

# ── 排序白名单 — SPEC 9.4 ──────────────────────────────────────────────────
#
# 每个查询显式声明允许排序的字段白名单（SPEC 9.4）。
# 客户端传入不在白名单内的排序字段返回 400（SPEC 9.4 / 23.3）。

_EXAMPLE_SORT_FIELDS = frozenset({"name", "created_at", "updated_at"})

# 路由前缀 — SPEC 9.1: 路由按业务模块分组。
_ROUTER_PREFIX = "/example/items"

router = APIRouter(prefix=_ROUTER_PREFIX, tags=["example"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_example_use_case(request: Request) -> ExampleItemUseCase:
    """构造 ``ExampleItemUseCase`` — 组合根装配（SPEC 5.2）.

    SPEC 5.2: "Composition Root 是唯一允许同时引用接口与具体实现
    并执行装配的位置"。此函数在 API 层执行装配，从 ``app.state``
    获取数据库引擎，构造 UoW 工厂和 Use Case。

    Router 通过 ``Depends(get_example_use_case)`` 获得 Use Case 实例，
    不直接接触 UoW、Repository 或 AsyncSession（SPEC 5.6）。
    """

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.example.handler import ExampleItemCreatedHandler

    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6: 一个 Use Case 对应一个 UoW。"""

        return SqlAlchemyUnitOfWork(engine)

    return ExampleItemUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        event_handlers=[ExampleItemCreatedHandler()],
    )


UseCaseDep = Annotated[ExampleItemUseCase, Depends(get_example_use_case)]
ContextDep = Annotated[UseCaseContext, Depends(create_use_case_context)]


# ── 路由定义 ───────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=ExampleItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建示例条目",
    operation_id="create_example_item",
)
async def create_item(
    request_body: ExampleItemCreateRequest,
    ctx: ContextDep,
    use_case: UseCaseDep,
) -> ExampleItemResponse:
    """创建示例条目 — HTTP 201（SPEC 9.3）.

    创建成功返回 201 和资源 Schema（SPEC 9.3: "创建成功返回 HTTP 201"）。
    名称冲突返回 409 problem+json（EXAMPLE.CONFLICT）。
    """

    return await use_case.create_item(ctx, request_body)


@router.get(
    "/{itemId}",
    response_model=ExampleItemResponse,
    summary="查询单个示例条目",
    operation_id="get_example_item",
)
async def get_item(
    ctx: ContextDep,
    use_case: UseCaseDep,
    item_id: Annotated[UUID, Path(alias="itemId", description="条目 ID")],
) -> ExampleItemResponse:
    """查询单个示例条目 — 不存在返回 404 problem+json（EXAMPLE.NOT_FOUND）。"""

    return await use_case.get_item(ctx, item_id)


@router.get(
    "",
    response_model=PageResponse[ExampleItemResponse],
    summary="分页查询示例条目列表",
    operation_id="list_example_items",
)
async def list_items(
    ctx: ContextDep,
    use_case: UseCaseDep,
    params: Annotated[PageParams, Depends()],
    sort: Annotated[
        list[SortField],
        Depends(sort_dependency(_EXAMPLE_SORT_FIELDS)),
    ],
) -> dict[str, object]:
    """分页查询示例条目列表 — SPEC 9.4 分页排序.

    分页参数: ``page``（默认 1）、``pageSize``（默认 20）。
    排序参数: ``sort``，逗号分隔，``-`` 前缀降序。
    排序字段白名单（camelCase）: ``name``、``createdAt``、``updatedAt``。
    """

    return await use_case.list_items(
        ctx,
        page=params.page,
        page_size=params.page_size,
        sort_fields=sort,
    )


@router.put(
    "/{itemId}",
    response_model=ExampleItemResponse,
    summary="更新示例条目",
    operation_id="update_example_item",
)
async def update_item(
    request_body: ExampleItemUpdateRequest,
    ctx: ContextDep,
    use_case: UseCaseDep,
    item_id: Annotated[UUID, Path(alias="itemId", description="条目 ID")],
) -> ExampleItemResponse:
    """更新示例条目 — 全量更新（PUT 语义，SPEC 9.2）。"""

    return await use_case.update_item(ctx, item_id, request_body)


@router.delete(
    "/{itemId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除示例条目",
    operation_id="delete_example_item",
)
async def delete_item(
    ctx: ContextDep,
    use_case: UseCaseDep,
    item_id: Annotated[UUID, Path(alias="itemId", description="条目 ID")],
) -> Response:
    """删除示例条目 — HTTP 204 无响应体（SPEC 9.3）。"""

    await use_case.delete_item(ctx, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
