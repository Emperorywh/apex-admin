"""系统配置请求与响应 Schema — SPEC 9.2 / 9.3 / 16.1 / 16.2.

SPEC 9.2: 创建、全量更新请求拒绝未知字段（``extra="forbid"``）。
SPEC 9.3: JSON 字段统一 snake_case。

SPEC 16.1:
  - 敏感配置 API 响应不回显明文（掩码）。
  - 配置值类型为 string / int / bool / json。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, Field

from app.core.api.schemas import StrictBaseModel

#: 敏感配置掩码值 — SPEC 16.1: "默认不回显"。
SENSITIVE_MASK = "***MASKED***"

#: 支持的配置值类型。
_VALID_TYPES = "string / int / bool / json"


class ConfigCreateRequest(StrictBaseModel):
    """创建配置项请求 — SPEC 9.2 / 16.1.

    属性:
        group:            配置分组，1-100 字符。
        key:              配置键，1-200 字符（分组内唯一）。
        value_type:       值类型（string / int / bool / json）。
        value:            配置值字符串（保存时按 value_type 校验）。
        is_sensitive:     是否为敏感配置（敏感配置加密存储且不回显）。
        is_core_security: 是否为核心安全配置（不可被普通后台覆盖）。
        description:      配置说明（可选）。
    """

    group: str = Field(
        min_length=1,
        max_length=100,
        description="配置分组",
    )
    key: str = Field(
        min_length=1,
        max_length=200,
        description="配置键（分组内唯一）",
    )
    value_type: str = Field(
        description=f"值类型（{_VALID_TYPES}）",
        pattern=r"^(string|int|bool|json)$",
    )
    value: str = Field(
        min_length=1,
        description="配置值字符串（保存时按 value_type 校验）",
    )
    is_sensitive: bool = Field(
        default=False,
        description="是否为敏感配置（加密存储且 API 不回显明文）",
    )
    is_core_security: bool = Field(
        default=False,
        description="是否为核心安全配置（不可被普通后台覆盖）",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="配置说明（可选）",
    )


class ConfigUpdateRequest(StrictBaseModel):
    """更新配置项请求 — SPEC 9.2 / 16.1.

    更新配置值和说明。分组和键不可变更。
    核心安全配置不可通过此端点更新。

    属性:
        value:       新配置值字符串（保存时按 value_type 校验）。
        description: 配置说明（可选）。
    """

    value: str = Field(
        min_length=1,
        description="新配置值字符串（保存时按 value_type 校验）",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="配置说明（可选）",
    )


class ConfigResponse(BaseModel):
    """配置项响应模型 — SPEC 9.3 / 16.1.

    SPEC 16.1: 敏感配置 API 响应不回显明文（掩码）。
    敏感配置的 ``value`` 字段返回 ``***MASKED***``。
    """

    model_config = {"extra": "forbid"}

    id: UUID
    group: str
    key: str
    value_type: str
    value: str
    is_sensitive: bool
    is_core_security: bool
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class ConfigGroupResponse(BaseModel):
    """配置分组响应 — SPEC 16.1 按分组管理."""

    model_config = {"extra": "forbid"}

    group: str
    item_count: int
