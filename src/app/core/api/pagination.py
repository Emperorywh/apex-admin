"""分页与排序框架 — SPEC 9.4.

SPEC 9.4 约定:
  - 页码分页参数固定为 ``page`` 和 ``page_size``，默认值分别为 1 和 20。
  - ``page`` 最小值为 1，``page_size`` 范围为 1 至 100。
  - 页码分页响应固定为 ``{items, total, page, page_size, pages}``。
  - 排序参数固定为 ``sort``，使用逗号分隔字段，前缀 ``-`` 表示降序，
    例如 ``-created_at,name``。
  - 排序字段使用每个查询显式声明的白名单，不在白名单内返回参数错误。
  - 禁止将客户端输入直接拼接为 SQL（排序字段经白名单校验后再使用）。
  - 游标分页必须使用独立响应模型，禁止与页码分页参数混用。
    当前无游标分页使用者，此规则以文档约束形式建立。

所有排序字段在解析阶段即验证白名单，确保后续构建 SQL 排序时只使用
已声明的安全字段名，不存在客户端输入直接进入 SQL 的路径。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

from app.core.errors.exceptions import ParameterError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

T = TypeVar("T")

# ── 分页参数 ──────────────────────────────────────────────────────────────


class PageParams:
    """页码分页查询参数 — SPEC 9.4.

    作为 FastAPI 依赖使用，从查询字符串提取 ``page`` 和 ``page_size``。
    SPEC 9.4 约定默认 page=1、page_size=20，page 最小 1，page_size 1-100。

    使用方式::

        @router.get("/items")
        async def list_items(params: Annotated[PageParams, Depends()]):
            offset = (params.page - 1) * params.page_size
            ...

    越界值由 FastAPI/Pydantic 的 Query 校验直接返回 422。
    """

    page: int
    page_size: int

    def __init__(
        self,
        page: Annotated[
            int,
            Query(ge=1, description="页码，从 1 开始"),
        ] = 1,
        page_size: Annotated[
            int,
            Query(ge=1, le=100, description="每页数量，范围 1-100"),
        ] = 20,
    ) -> None:
        """初始化分页参数，校验 page ≥ 1 且 page_size ∈ [1, 100]。"""

        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        """计算 SQL OFFSET 值（零基）。"""

        return (self.page - 1) * self.page_size

    def __iter__(self) -> Iterator[int]:
        """支持 ``page, page_size = params`` 解包。"""

        yield self.page
        yield self.page_size


def total_pages(total: int, page_size: int) -> int:
    """计算总页数 — SPEC 9.4.

    参数:
        total:     符合条件的记录总数。
        page_size: 每页数量。

    返回:
        总页数；total 为 0 或 page_size ≤ 0 时返回 0。
    """

    if page_size <= 0 or total <= 0:
        return 0
    return math.ceil(total / page_size)


# ── 分页响应 ──────────────────────────────────────────────────────────────


class PageResponse(BaseModel, Generic[T]):  # noqa: UP046
    """页码分页响应模型 — SPEC 9.4 固定结构.

    响应固定包含 ``{items, total, page, page_size, pages}`` 五个字段，
    不使用 ``{code, message, data}`` 成功信封（SPEC 9.3）。

    泛型参数 ``T`` 为 items 元素的 Schema 类型，使用方式::

        class ItemOut(StrictBaseModel):
            id: str
            name: str

        response = PageResponse[ItemOut](
            items=[...], total=100, page=1, page_size=20, pages=5,
        )
    """

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


# ── 排序框架 ──────────────────────────────────────────────────────────────


class SortOrder(StrEnum):
    """排序方向枚举。"""

    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True)
class SortField:
    """已解析的单个排序字段。

    属性:
        name:  字段名（已通过白名单校验），可直接用于构建 SQL ORDER BY。
        order: 排序方向。
    """

    name: str
    order: SortOrder


def parse_sort(
    sort: str | None,
    allowed_fields: frozenset[str],
) -> list[SortField]:
    """解析排序查询参数并校验白名单 — SPEC 9.4.

    解析规则:
      - 逗号分隔多个字段，例如 ``-created_at,name``。
      - ``-`` 前缀表示降序，否则为升序。
      - 空白段（连续逗号或前后空白）被忽略。
      - None 或空字符串返回空列表（不排序）。

    安全规则:
      - 每个字段必须在 ``allowed_fields`` 白名单内，否则抛出
        ``ParameterError``（400 problem+json），防止客户端输入直接
        拼接为 SQL（SPEC 9.4 / 23.3）。

    参数:
        sort:           原始排序字符串。
        allowed_fields: 允许排序的字段白名单（每个查询显式声明）。

    返回:
        按声明顺序排列的排序字段列表。

    抛出:
        ParameterError: 排序字段不在白名单内。
    """

    if sort is None or not sort.strip():
        return []

    result: list[SortField] = []
    for raw in sort.split(","):
        part = raw.strip()
        if not part:
            continue

        # ``-`` 前缀 → 降序
        if part.startswith("-"):
            name = part[1:].strip()
            order = SortOrder.DESC
        else:
            name = part
            order = SortOrder.ASC

        # 白名单校验 — 阻止非声明字段进入排序（SPEC 9.4 / 23.3）
        if name not in allowed_fields:
            raise ParameterError(
                f"排序字段 '{name}' 不在允许的白名单内",
            )

        result.append(SortField(name=name, order=order))

    return result


def sort_dependency(
    allowed_fields: frozenset[str],
) -> Callable[..., list[SortField]]:
    """排序参数依赖工厂 — SPEC 9.4.

    每个查询声明自己的排序白名单，返回一个 FastAPI 依赖函数。
    非白名单字段经 ``parse_sort`` 抛出 ``ParameterError``，
    由异常处理器转换为 400 problem+json。

    使用方式::

        @router.get("/items")
        async def list_items(
            sort: Annotated[
                list[SortField],
                Depends(sort_dependency(frozenset({"name", "created_at"}))),
            ],
        ):
            ...

    参数:
        allowed_fields: 允许排序的字段白名单。

    返回:
        FastAPI 依赖函数，从 ``sort`` 查询参数解析排序字段。
    """

    def _dependency(
        sort: Annotated[
            str | None,
            Query(description="排序字段，逗号分隔，- 前缀表示降序"),
        ] = None,
    ) -> list[SortField]:
        return parse_sort(sort, allowed_fields)

    return _dependency
