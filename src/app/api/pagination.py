"""页码分页参数与响应模型（SPEC §9.4）。

提供可复用的分页查询参数模型、分页响应泛型模型和 FastAPI 查询依赖。

分页约定（SPEC §9.4）：
    - 页码分页参数固定为 ``page`` 和 ``page_size``，默认值分别为 1 和 20
    - ``page`` 最小值为 1
    - ``page_size`` 范围为 1 至 100
    - 分页响应固定为 ``{items, total, page, page_size, pages}``

本模块不依赖 FastAPI 的请求对象，只提供数据模型和依赖工厂，
便于在单元测试中独立验证分页约束。
"""

from __future__ import annotations

import math

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

# 默认每页条数（SPEC §9.4：默认值 20）
DEFAULT_PAGE_SIZE: int = 20

# 最大每页条数（SPEC §9.4：最大 100）
MAX_PAGE_SIZE: int = 100


class PaginationParams(BaseModel):
    """页码分页查询参数（SPEC §9.4）。

    所有列表端点通过 ``Depends(get_pagination_params)`` 获取此对象。

    约束：
        - ``page`` 最小值为 1（SPEC §9.4）
        - ``page_size`` 范围为 1 至 100（SPEC §9.4）

    Attributes:
        page: 当前页码，从 1 开始
        page_size: 每页条数，1 至 100
    """

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


async def get_pagination_params(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="每页条数，1 至 100",
    ),
) -> PaginationParams:
    """FastAPI 查询依赖：从 ``page`` 和 ``page_size`` 查询参数构造 PaginationParams。

    用法::

        @router.get("/items")
        async def list_items(
            pagination: PaginationParams = Depends(get_pagination_params),
        ) -> Page[ItemResponse]:
            ...

    Args:
        page: 页码查询参数，最小 1，默认 1
        page_size: 每页条数查询参数，1 至 100，默认 20

    Returns:
        已校验的 :class:`PaginationParams` 实例
    """
    return PaginationParams(page=page, page_size=page_size)


class Page[T](BaseModel):
    """页码分页响应模型（SPEC §9.4）。

    所有列表端点的成功响应使用此模型，响应体固定为::

        {
            "items": [...],
            "total": 100,
            "page": 1,
            "page_size": 20,
            "pages": 5
        }

    不使用 ``{code, message, data}`` 成功信封（SPEC §9.3）。
    ``items`` 的元素类型由泛型参数 ``T`` 指定，通常为资源响应 Schema。

    Attributes:
        items: 当前页资源列表
        total: 符合筛选条件的资源总数
        page: 当前页码
        page_size: 每页条数
        pages: 总页数
    """

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


def paginate[T](items: list[T], total: int, pagination: PaginationParams) -> Page[T]:
    """根据资源列表和分页参数构造分页响应（SPEC §9.4）。

    总页数使用 ``ceil(total / page_size)`` 计算，``total`` 为 0 时 ``pages`` 为 0。

    Args:
        items: 当前页的资源列表
        total: 符合筛选条件的资源总数
        pagination: 分页查询参数

    Returns:
        填充好的 :class:`Page` 实例
    """
    pages = math.ceil(total / pagination.page_size) if total > 0 else 0
    return Page(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )
