"""用户领域实体与状态枚举 — SPEC 11.2 / 5.2.

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2: "领域规则不得依赖 FastAPI、ORM、HTTP 或具体存储 SDK"）。

用户状态使用稳定字符串编码（SPEC 8.3: "枚举值具有稳定编码"），
``UserStatus`` 的值即为持久化到数据库和返回给客户端的稳定编码。

DTO、API Schema、领域对象和 ORM 模型职责分离（SPEC 5.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class UserStatus(StrEnum):
    """用户状态枚举 — 稳定字符串编码（SPEC 8.3 / 11.2）.

    SPEC 11.1 定义了启用和禁用两个生命周期动作，对应两个稳定状态编码。
    SPEC 11.3: "默认优先采用禁用或注销，而不是直接删除用户"。
    禁用（DISABLED）是软停用语义——用户不可登录但数据保留。

    属性:
        ACTIVE:   启用状态——用户可正常登录和使用系统。
        DISABLED: 禁用状态——用户不可登录，数据保留以备审计。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class User:
    """用户领域实体 — SPEC 11.2.

    SPEC 11.2 用户字段:
      - 用户名（登录账号）、显示名称。
      - 密码哈希（Argon2id，SPEC 12.1）。
      - 用户状态。
      - 手机号和邮箱（按项目需要启用，可为空）。
      - 最近登录时间、密码更新时间。
      - 创建/更新时间。
      - 创建人/更新人（审计需要记录）。

    此实体为不可变值对象，Use Case 通过创建新实例替换旧实例来表达状态变更
    （SPEC 5.2: 显式状态、不可变领域对象）。

    属性:
        id:                  全局唯一标识（UUID v4）。
        username:            用户名/登录账号（全局唯一）。
        display_name:        显示名称。
        password_hash:       Argon2id 密码哈希（PHC 格式字符串）。
        status:              用户状态。
        phone:               手机号（可为空）。
        email:               邮箱（可为空）。
        last_login_at:       最近登录时间（UTC，可为空）。
        password_updated_at: 密码更新时间（UTC，可为空）。
        created_at:          创建时间（UTC）。
        updated_at:          更新时间（UTC）。
        created_by:          创建人标识（可为空）。
        updated_by:          更新人标识（可为空）。
    """

    id: UUID
    username: str
    display_name: str
    password_hash: str
    status: UserStatus
    phone: str | None
    email: str | None
    last_login_at: datetime | None
    password_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None
