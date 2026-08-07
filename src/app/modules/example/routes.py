"""示例模块路由（SPEC §5.2、§9.1）。

Router 挂载在 ``/api/v1/examples`` 前缀下，只获得 Use Case（Application
Service），不获得 UoW、AsyncSession 或提交接口（SPEC §5.6）。

依赖注入通过 ``app.state.db_pool_provider`` 获取数据库引擎，
经 :func:`~app.modules.example.infrastructure.wiring.create_example_service`
装配完整服务链后提供给端点。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.pagination import Page, PaginationParams, get_pagination_params, paginate
from app.api.schemas import BaseResponseModel
from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider
from app.modules.example.application.schemas import (
    CreateExampleRequest,
    ExampleResponse,
)
from app.modules.example.application.service import ExampleService
from app.modules.example.infrastructure.wiring import create_example_service

router = APIRouter(prefix="/examples", tags=["examples"])


def _get_engine(request: Request) -> AsyncEngine:
    """从应用状态获取数据库引擎。

    Router 端点通过 FastAPI 依赖注入获取引擎，再装配服务。
    数据库未就绪时返回 503。

    Raises:
        HTTPException: 数据库连接池未配置或未初始化时返回 503
    """
    provider = cast(
        "SqlAlchemyDbPoolProvider | None",
        getattr(request.app.state, "db_pool_provider", None),
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库未配置",
        )
    engine = provider.engine
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库连接池未初始化",
        )
    return engine


def get_example_service(request: Request) -> ExampleService:
    """FastAPI 依赖：装配并返回示例服务实例。

    Router 端点通过 ``Depends(get_example_service)`` 获取服务。
    服务实例轻量（仅持有工厂引用），每次请求构造。
    """
    engine = _get_engine(request)
    return create_example_service(engine)


class CreateExampleResponse(BaseResponseModel):
    """创建示例项目成功响应（SPEC §9.3：201 返回资源 Schema）。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str


@router.post(
    "",
    summary="创建示例项目",
    description=(
        "创建一个新的示例项目。"
        "此端点演示 Router → Use Case → Domain Policy → Repository → "
        "Database 的完整调用流，包含领域事件发布和事务提交。"
        "成功返回 HTTP 201 和创建的资源。"
    ),
    status_code=status.HTTP_201_CREATED,
)
async def create_example(
    request_body: CreateExampleRequest,
    service: ExampleService = Depends(get_example_service),  # noqa: B008
) -> CreateExampleResponse:
    """创建示例项目。"""
    item = await service.create_item(
        name=request_body.name,
        current_time=datetime.now(UTC),
    )
    return CreateExampleResponse(id=item.id, name=item.name)


@router.get(
    "",
    summary="查询示例项目列表",
    description=(
        "分页查询全部示例项目。"
        "此端点演示分页参数解析、Repository 查询和 Page 响应模式。"
        "响应使用标准分页结构 {items, total, page, page_size, pages}。"
    ),
)
async def list_examples(
    pagination: PaginationParams = Depends(get_pagination_params),  # noqa: B008
    service: ExampleService = Depends(get_example_service),  # noqa: B008
) -> Page[ExampleResponse]:
    """分页查询示例项目列表。"""
    items, total = await service.list_items(
        page=pagination.page,
        page_size=pagination.page_size,
    )
    responses = [
        ExampleResponse(
            id=item.id,
            name=item.name,
            created_at=item.created_at,
        )
        for item in items
    ]
    return paginate(responses, total, pagination)
