"""API 通用规范构建块 — SPEC 9.1 / 9.2 / 9.3 / 9.4 / 9.6.

此包承载跨模块复用的 API 约定基础设施，不属于任何具体业务路由。
各业务模块的 Router 从此包引入分页参数、排序解析、Schema 基类和
序列化约定，以统一方式满足 SPEC 第 9 节的 API 通用规范。

公开 API:
  - 分页: ``PageParams``, ``PageResponse``, ``total_pages``
  - 排序: ``SortField``, ``SortOrder``, ``parse_sort``, ``sort_dependency``
  - Schema: ``StrictBaseModel``
  - OpenAPI: ``OPENAPI_TAGS``, ``build_openapi_kwargs``
"""

from app.core.api.openapi import OPENAPI_TAGS, build_openapi_kwargs
from app.core.api.pagination import (
    PageParams,
    PageResponse,
    SortField,
    SortOrder,
    parse_sort,
    sort_dependency,
    total_pages,
)
from app.core.api.schemas import StrictBaseModel

__all__ = [
    "OPENAPI_TAGS",
    "PageParams",
    "PageResponse",
    "SortField",
    "SortOrder",
    "StrictBaseModel",
    "build_openapi_kwargs",
    "parse_sort",
    "sort_dependency",
    "total_pages",
]
