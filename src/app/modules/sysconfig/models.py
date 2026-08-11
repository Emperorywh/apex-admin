"""系统配置领域实体与状态枚举 — SPEC 16.1 / 16.2 / 5.2.

SPEC 16.1 配置项管理:
  - 配置项具有分组、组内唯一键、类型化值。
  - 配置值类型为 string / int / bool / json。
  - 敏感配置加密存储且默认不回显。
  - 核心安全配置不得由普通后台配置随意覆盖。

SPEC 16.2 配置读取:
  - 业务模块只读取自己声明依赖的配置键。
  - 不提供隐式全局配置读取对象。

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class ConfigType(StrEnum):
    """配置值类型枚举 — SPEC 16.1.

    SPEC 16.1: "配置值具有明确类型"。
    支持 string / int / bool / json 四种类型。

    属性:
        STRING: 字符串类型。
        INT:    整数类型。
        BOOL:   布尔类型（存储为 "true" / "false"）。
        JSON:   JSON 类型（存储为合法 JSON 字符串）。
    """

    STRING = "string"
    INT = "int"
    BOOL = "bool"
    JSON = "json"


class ConfigStatus(StrEnum):
    """配置项状态枚举 — SPEC 16.1.

    SPEC 16.1: "启用和禁用配置项"。

    属性:
        ACTIVE:   启用状态——配置有效。
        DISABLED: 禁用状态——配置失效。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ConfigItem:
    """系统配置项领域实体 — SPEC 16.1.

    SPEC 16.1 配置项管理:
      - 分组与组内唯一键。
      - 类型化值，保存时类型校验。
      - 敏感配置加密存储。
      - 核心安全配置保护。

    属性:
        id:              全局唯一标识（UUID）。
        group:           配置分组。
        key:             配置键（分组内唯一）。
        value_type:      值类型（STRING / INT / BOOL / JSON）。
        stored_value:    存储值——敏感配置为加密密文，非敏感为原始字符串。
        is_sensitive:    是否为敏感配置（敏感配置加密存储且 API 不回显明文）。
        is_core_security: 是否为核心安全配置（不可被普通后台配置覆盖）。
        description:     配置说明（可选）。
        status:          配置状态（ACTIVE / DISABLED）。
        created_at:      创建时间（UTC）。
        updated_at:      更新时间（UTC）。
        created_by:      创建人标识（可为空）。
        updated_by:      更新人标识（可为空）。
    """

    id: UUID
    group: str
    key: str
    value_type: ConfigType
    stored_value: str
    is_sensitive: bool
    is_core_security: bool
    description: str | None
    status: ConfigStatus
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None
