"""示例模块应用服务 / Use Case（SPEC §5.2、§5.6、§5.7）。

Use Case 编排领域策略、持久化和事件发布：

1. 在 ``async with`` 上下文中打开 :class:`ExampleUnitOfWork`
2. 调用领域策略校验业务规则
3. 通过 Repository 端口执行数据操作
4. 收集领域事件，在提交前通过 :class:`~app.events.dispatcher.TransactionalEventDispatcher` 调度
5. 退出 ``async with`` 时由 UoW 统一提交（SPEC §5.6）

Router 只获得 Use Case，不获得 UoW、AsyncSession 或提交接口（SPEC §5.6）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.errors import ParameterError
from app.events.dispatcher import TransactionalEventDispatcher
from app.modules.example.application.port import (
    ExampleApplicationPort,
    ExampleUnitOfWork,
)
from app.modules.example.domain.events import ExampleItemCreated
from app.modules.example.domain.model import ExampleItem
from app.modules.example.domain.policy import ExampleNamePolicy


class ExampleService(ExampleApplicationPort):
    """示例模块应用服务（SPEC §5.2）。

    实现创建和查询示例项目的 Use Case。每个写 Use Case 在独立的
    Unit of Work 中执行，退出时统一提交或回滚。

    Args:
        uow_factory: 工作单元工厂，每次调用返回新的 :class:`ExampleUnitOfWork`
        event_dispatcher: 事务内事件调度器，收集并调度领域事件
    """

    def __init__(
        self,
        uow_factory: Callable[[], ExampleUnitOfWork],
        event_dispatcher: TransactionalEventDispatcher,
    ) -> None:
        self._uow_factory = uow_factory
        self._event_dispatcher = event_dispatcher

    async def create_item(self, *, name: str, current_time: datetime) -> ExampleItem:
        """创建示例项目 Use Case（SPEC §5.6）。

        1. 校验名称（领域策略）
        2. 创建领域实体
        3. 通过 Repository 持久化
        4. 收集并调度领域事件（提交前同步执行）

        Args:
            name: 项目名称
            current_time: 当前 UTC 时间

        Returns:
            已创建的 :class:`ExampleItem`

        Raises:
            ParameterError: 名称不合规
        """
        async with self._uow_factory() as uow:
            try:
                ExampleNamePolicy.validate(name)
            except ValueError as exc:
                raise ParameterError(
                    str(exc),
                    code="EXAMPLE.INVALID_NAME",
                ) from exc

            item = ExampleItem.new(name=name, created_at=current_time)
            await uow.examples.add(item)

            self._event_dispatcher.collect(
                ExampleItemCreated(
                    occurred_at=current_time,
                    item_id=item.id,
                    name=item.name,
                )
            )
            await self._event_dispatcher.flush(uow)

            return item
        # UoW 在无异常退出时自动提交（SPEC §5.6）

    async def list_items(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ExampleItem], int]:
        """查询示例项目列表 Use Case。

        Args:
            page: 页码，从 1 开始
            page_size: 每页条数

        Returns:
            (当前页实体列表, 总数) 二元组
        """
        async with self._uow_factory() as uow:
            total = await uow.examples.count()
            offset = (page - 1) * page_size
            items = await uow.examples.list_paginated(offset, page_size)
            return items, total
