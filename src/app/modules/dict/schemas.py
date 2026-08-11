"""数据字典请求与响应 Schema — SPEC 9.2 / 9.3 / 17.1 / 17.2.

SPEC 9.2: 创建、全量更新请求拒绝未知字段（``extra="forbid"``）。
SPEC 9.3: JSON 字段统一 snake_case。

SPEC 17.1:
  - 字典类型创建/更新。
  - 字典编码保持稳定和唯一。

SPEC 17.2:
  - 字典项支持显示文本（label）、稳定值（value）、排序（sort_order）和
    扩展元数据（metadata）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, Field

from app.core.api.schemas import StrictBaseModel

# ── 字典类型请求/响应 ──────────────────────────────────────────────────────


class DictTypeCreateRequest(StrictBaseModel):
    """创建字典类型请求 — SPEC 9.2 / 17.1.

    属性:
        code:        稳定编码（全局唯一），1-100 字符，小写字母/数字/下划线。
        name:        显示名称，1-200 字符。
        description: 描述说明（可选），最长 1000 字符。
    """

    code: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="字典编码（全局唯一，小写字母/数字/下划线）",
    )
    name: str = Field(
        min_length=1,
        max_length=200,
        description="字典类型显示名称",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="描述说明（可选）",
    )


class DictTypeUpdateRequest(StrictBaseModel):
    """更新字典类型请求 — SPEC 9.2 / 17.1.

    更新显示名称和描述。编码不可变更（稳定标识）。

    属性:
        name:        显示名称，1-200 字符。
        description: 描述说明（可选），最长 1000 字符。
    """

    name: str = Field(
        min_length=1,
        max_length=200,
        description="字典类型显示名称",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="描述说明（可选）",
    )


class DictTypeResponse(BaseModel):
    """字典类型响应模型 — SPEC 9.3 / 17.1."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


# ── 字典项请求/响应 ────────────────────────────────────────────────────────


class DictItemCreateRequest(StrictBaseModel):
    """创建字典项请求 — SPEC 9.2 / 17.2.

    SPEC 17.2: 支持显示文本、稳定值、排序和扩展元数据。

    属性:
        label:      显示文本（人类可读），1-200 字符。
        value:      稳定值（业务持久化），1-200 字符。
        sort_order: 排序序号（升序），默认 0。
        metadata:   扩展元数据（JSON 对象），默认空字典。
        description: 描述说明（可选），最长 1000 字符。
    """

    label: str = Field(
        min_length=1,
        max_length=200,
        description="显示文本",
    )
    value: str = Field(
        min_length=1,
        max_length=200,
        description="稳定值（业务持久化此值，不变更）",
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="排序序号（升序）",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展元数据",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="描述说明（可选）",
    )


class DictItemUpdateRequest(StrictBaseModel):
    """更新字典项请求 — SPEC 9.2 / 17.2.

    更新显示文本、稳定值、排序和扩展元数据。

    属性:
        label:      显示文本，1-200 字符。
        value:      稳定值，1-200 字符。
        sort_order: 排序序号（升序）。
        metadata:   扩展元数据。
        description: 描述说明（可选）。
    """

    label: str = Field(
        min_length=1,
        max_length=200,
        description="显示文本",
    )
    value: str = Field(
        min_length=1,
        max_length=200,
        description="稳定值",
    )
    sort_order: int = Field(
        ge=0,
        description="排序序号（升序）",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展元数据",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="描述说明（可选）",
    )


class DictItemResponse(BaseModel):
    """字典项响应模型 — SPEC 9.3 / 17.2."""

    model_config = {"extra": "forbid"}

    id: UUID
    dict_type_id: UUID
    label: str
    value: str
    sort_order: int
    metadata: dict[str, Any]
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime
