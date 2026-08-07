"""API 层：请求/响应模型、分页/排序助手、异常处理器和路由基础设施（SPEC §9）。

API 层负责 HTTP 请求和响应的序列化与反序列化、参数校验和异常到
RFC 9457 ProblemDetail 的转换。API 层不直接访问数据库，不提交事务。
"""

from __future__ import annotations

from app.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    PaginationParams,
    get_pagination_params,
    paginate,
)
from app.api.schemas import BaseRequestModel, BaseResponseModel
from app.api.sorting import SortInstruction, parse_sort

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "BaseRequestModel",
    "BaseResponseModel",
    "Page",
    "PaginationParams",
    "SortInstruction",
    "get_pagination_params",
    "paginate",
    "parse_sort",
]
