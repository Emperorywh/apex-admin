"""示例领域实体 — SPEC 5.2.

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2: "领域规则不得依赖 FastAPI、ORM、HTTP 或具体存储 SDK"）。

DTO、API Schema、领域对象和 ORM 模型职责分离（SPEC 5.2）。
本模块定义领域对象，``schemas.py`` 定义 API Schema，``orm.py`` 定义 ORM 模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


@dataclass(frozen=True)
class ExampleItem:
    """示例条目领域实体 — 不可变值对象.

    SPEC 5.2: "不强制为纯 CRUD 模块创建没有实际领域逻辑的复杂领域模型"。
    本实体仅承载数据，不含行为方法，适合纯 CRUD 场景。

    属性:
        id:          全局唯一标识（UUID v4）。
        name:        条目名称（全局唯一，数据库唯一约束保证）。
        description: 条目描述（可为空）。
        created_at:  创建时间（UTC，带时区）。
        updated_at:  更新时间（UTC，带时区）。
    """

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
