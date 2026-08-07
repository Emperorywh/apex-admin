"""示例模块 Application Port（SPEC §5.2、§5.5、§5.6）。

定义三种端口：

1. :class:`ExampleApplicationPort` — 模块公开的应用服务接口，
   其他模块依赖此接口与示例模块协作（SPEC §5.5 ``application_port``）。
2. :class:`ExampleRepository` — 数据访问端口，Use Case 依赖此接口，
   Infrastructure 层的 Repository Adapter 实现此接口（SPEC §5.2 调用流）。
3. :class:`ExampleUnitOfWork` — 扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，
   在事务作用域内提供 :class:`ExampleRepository` 访问（SPEC §5.6）。

端口只定义接口，不包含运行时副作用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.modules.example.domain.model import ExampleItem
from app.ports.unit_of_work import UnitOfWork


class ExampleApplicationPort(ABC):
    """示例模块公开 Application Port（SPEC §5.5）。

    其他模块依赖此接口与示例模块协作。跨模块调用只能通过公开的
    Application Port 完成（SPEC §5.1）。

    此接口不得提交、回滚或开启隐藏事务（SPEC §5.6）。
    """

    @abstractmethod
    async def create_item(self, *, name: str, current_time: datetime) -> ExampleItem:
        """创建示例项目。

        Args:
            name: 项目名称
            current_time: 当前 UTC 时间

        Returns:
            已创建的 :class:`ExampleItem`
        """

    @abstractmethod
    async def list_items(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ExampleItem], int]:
        """分页查询示例项目列表。

        Args:
            page: 页码，从 1 开始
            page_size: 每页条数

        Returns:
            (当前页实体列表, 总数) 二元组
        """


class ExampleRepository(ABC):
    """示例模块数据访问端口（SPEC §5.2）。

    Use Case 依赖此接口执行持久化操作。Infrastructure 层的
    :class:`~app.modules.example.infrastructure.repository.SqlAlchemyExampleRepository`
    实现此接口。

    所有方法在当前 Unit of Work 的事务作用域内执行，
    不自行提交或回滚（SPEC §5.6）。
    """

    @abstractmethod
    async def add(self, entity: ExampleItem) -> None:
        """将实体添加到当前事务作用域。

        Args:
            entity: 待持久化的示例实体
        """

    @abstractmethod
    async def get_by_id(self, item_id: UUID) -> ExampleItem | None:
        """按 ID 查询单个实体。

        Args:
            item_id: 实体 UUID

        Returns:
            匹配的实体；不存在时返回 None
        """

    @abstractmethod
    async def count(self) -> int:
        """返回实体总数。

        Returns:
            当前持久化的示例实体数量
        """

    @abstractmethod
    async def list_paginated(self, offset: int, limit: int) -> list[ExampleItem]:
        """分页查询实体列表。

        Args:
            offset: 跳过的记录数
            limit: 返回的最大记录数

        Returns:
            当前页的实体列表
        """


class ExampleUnitOfWork(UnitOfWork):
    """示例模块工作单元端口（SPEC §5.6）。

    扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，在事务作用域内
    提供 :class:`ExampleRepository` 访问。Use Case 通过此接口在
    ``async with`` 上下文中执行数据操作，退出时由底层实现统一提交或回滚。

    Infrastructure 层的
    :class:`~app.modules.example.infrastructure.unit_of_work.SqlAlchemyExampleUnitOfWork`
    实现此端口。
    """

    @property
    @abstractmethod
    def examples(self) -> ExampleRepository:
        """当前事务作用域的示例 Repository。"""
