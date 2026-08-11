"""数据字典领域实体与状态枚举 — SPEC 17.1 / 17.2 / 5.2.

SPEC 17.1 字典类型:
  - 创建/查询/更新/启用禁用字典类型。
  - 字典编码保持稳定和唯一。
  - 已被业务引用的字典类型具有删除保护。

SPEC 17.2 字典项:
  - 支持显示文本、稳定值、排序和扩展元数据。
  - 业务数据持久化稳定值，而不是展示文本。

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class DictTypeStatus(StrEnum):
    """字典类型状态枚举 — SPEC 17.1.

    SPEC 17.1: "启用和禁用字典类型"。

    属性:
        ACTIVE:   启用状态——字典类型可用。
        DISABLED: 禁用状态——字典类型已停用。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


class DictItemStatus(StrEnum):
    """字典项状态枚举 — SPEC 17.2.

    SPEC 17.2: "启用和禁用字典项"。

    属性:
        ACTIVE:   启用状态——字典项有效。
        DISABLED: 禁用状态——字典项停用。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class DictType:
    """字典类型领域实体 — SPEC 17.1.

    SPEC 17.1:
      - 字典编码保持稳定和唯一。
      - 创建/查询/更新/启用禁用。
      - 已被业务引用的字典类型具有删除保护。

    属性:
        id:          全局唯一标识（UUID）。
        code:        稳定编码（全局唯一，不随显示名变更而改变）。
        name:        显示名称。
        description: 描述说明（可选）。
        status:      状态（ACTIVE / DISABLED）。
        created_at:  创建时间（UTC）。
        updated_at:  更新时间（UTC）。
        created_by:  创建人标识（可为空）。
        updated_by:  更新人标识（可为空）。
    """

    id: UUID
    code: str
    name: str
    description: str | None
    status: DictTypeStatus
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


@dataclass(frozen=True)
class DictItem:
    """字典项领域实体 — SPEC 17.2.

    SPEC 17.2:
      - 支持显示文本、稳定值、排序和扩展元数据。
      - 业务数据持久化稳定值，而不是展示文本。
      - 创建/查询/更新/启用禁用。

    属性:
        id:           全局唯一标识（UUID）。
        dict_type_id: 所属字典类型 ID。
        label:        显示文本（人类可读，可随 UI 需求变更）。
        value:        稳定值（业务持久化此值，不变更）。
        sort_order:   排序序号（升序，默认 0）。
        metadata_:    扩展元数据（JSON 对象，可为空字典）。
        description:  描述说明（可选）。
        status:       状态（ACTIVE / DISABLED）。
        created_at:   创建时间（UTC）。
        updated_at:   更新时间（UTC）。
        created_by:   创建人标识（可为空）。
        updated_by:   更新人标识（可为空）。
    """

    id: UUID
    dict_type_id: UUID
    label: str
    value: str
    sort_order: int
    metadata_: dict[str, Any]
    description: str | None
    status: DictItemStatus
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None
