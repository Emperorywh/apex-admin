"""示例 Repository Port — SPEC 5.2 / 5.6.

SPEC 5.2: "Repository、Unit of Work、文件存储和外部服务 Port
由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port（SPEC 5.2: "Infrastructure 只实现内层 Port"）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.api.pagination import SortField
    from app.modules.example.models import ExampleItem


class ExampleItemRepository(ABC):
    """示例条目 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型（如 AsyncSession、Select 等），
    确保内层不感知具体 ORM（SPEC 5.2 / 8.1）。

    返回值为领域实体 ``ExampleItem``，不是 ORM 模型，
    实现 DTO/领域对象/ORM 模型职责分离（SPEC 5.2）。
    """

    @abstractmethod
    async def add(self, item: ExampleItem) -> None:
        """添加新条目到当前事务.

        参数:
            item: 待添加的领域实体。
        """

    @abstractmethod
    async def get_by_id(self, item_id: UUID) -> ExampleItem | None:
        """按 ID 查询条目.

        参数:
            item_id: 条目 UUID。

        返回:
            领域实体；不存在时返回 None。
        """

    @abstractmethod
    async def list_items(
        self,
        *,
        offset: int,
        limit: int,
        sort_fields: list[SortField],
    ) -> tuple[list[ExampleItem], int]:
        """分页查询条目列表.

        SPEC 9.4: 排序字段使用白名单校验后的 ``SortField`` 列表。

        参数:
            offset:      SQL OFFSET 值（零基）。
            limit:       SQL LIMIT 值。
            sort_fields: 已解析的排序字段列表（白名单已校验）。

        返回:
            (条目列表, 总数) 元组。
        """

    @abstractmethod
    async def save(self, item: ExampleItem) -> None:
        """保存条目变更到当前事务.

        参数:
            item: 更新后的领域实体。
        """

    @abstractmethod
    async def delete_by_id(self, item_id: UUID) -> bool:
        """按 ID 删除条目.

        参数:
            item_id: 条目 UUID。

        返回:
            删除成功返回 True；条目不存在返回 False。
        """
