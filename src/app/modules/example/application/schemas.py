"""示例模块请求/响应 Schema（SPEC §9.2、§9.3）。

继承 :class:`~app.api.schemas.BaseRequestModel` 和
:class:`~app.api.schemas.BaseResponseModel`，确保未知字段被拒绝
和 snake_case 命名一致。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict

from app.api.schemas import BaseRequestModel, BaseResponseModel


class CreateExampleRequest(BaseRequestModel):
    """创建示例项目请求 Schema（SPEC §9.2）。

    Attributes:
        name: 示例项目名称，非空且不超过 100 字符
    """

    name: str


class ExampleResponse(BaseResponseModel):
    """示例项目响应 Schema（SPEC §9.3）。

    响应直接返回资源 Schema，不使用 ``{code, message, data}`` 信封。
    时间字段使用带时区的 ISO 8601 字符串（SPEC §9.3、§6.3）。

    ``from_attributes=True`` 允许从领域实体（dataclass）直接构造响应。

    Attributes:
        id: 实体 UUID
        name: 名称
        created_at: 创建时间（UTC）
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
