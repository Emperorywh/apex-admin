"""示例模块请求与响应 Schema — SPEC 9.2 / 9.3.

SPEC 9.2 约定:
  - 创建、全量更新和部分更新请求统一拒绝未知字段（``extra="forbid"``）。
  - 请求参数使用明确的 Schema 校验。
  - 创建、更新、查询和响应模型按职责区分。

SPEC 9.3 约定:
  - JSON 字段统一使用 camelCase。
  - 时间字段统一为带时区的 ISO 8601 字符串。
  - 普通成功响应直接返回资源 Schema，不使用成功信封。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import Field

from app.core.api.schemas import ApiModel, StrictBaseModel


class ExampleItemCreateRequest(StrictBaseModel):
    """创建示例条目请求 — SPEC 9.2.

    继承 ``StrictBaseModel``（``extra="forbid"``），
    携带未知字段的请求返回 422（SPEC 9.2）。

    属性:
        name:        条目名称，1-200 字符。
        description: 条目描述，最多 1000 字符，可为空。
    """

    name: str = Field(min_length=1, max_length=200, description="条目名称")
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="条目描述",
    )


class ExampleItemUpdateRequest(StrictBaseModel):
    """全量更新示例条目请求 — SPEC 9.2.

    PUT 语义：所有字段必填。
    继承 ``StrictBaseModel``（``extra="forbid"``）。
    """

    name: str = Field(min_length=1, max_length=200, description="条目名称")
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="条目描述",
    )


class ExampleItemResponse(ApiModel):
    """示例条目响应模型 — SPEC 9.3.

    SPEC 9.3: "普通成功响应直接返回资源 Schema，不使用成功信封"。
    响应模型不使用 ``extra="forbid"``（响应可能有扩展字段）。
    JSON 字段使用 camelCase，时间为带时区 ISO 8601 字符串。
    """

    model_config = {"extra": "forbid"}

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
