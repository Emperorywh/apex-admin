"""示例模块 Repository Adapter（SPEC §5.2）。

实现 :class:`~app.modules.example.application.port.ExampleRepository` 端口，
使用 SQLAlchemy AsyncSession 执行数据访问。Repository 不自行提交或回滚，
所有操作在传入的 Session（由 UoW 管理）的事务作用域内执行（SPEC §5.6）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.example.application.port import ExampleRepository
from app.modules.example.domain.model import ExampleItem
from app.modules.example.infrastructure.models import ExampleItemModel


class SqlAlchemyExampleRepository(ExampleRepository):
    """基于 SQLAlchemy 的示例 Repository。

    每个实例在构造时接收一个 :class:`~sqlalchemy.ext.asyncio.AsyncSession`，
    该 Session 由上层 Unit of Work 创建和管理。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: ExampleItem) -> None:
        """将实体添加到当前 Session。"""
        model = ExampleItemModel.from_entity(entity)
        self._session.add(model)

    async def get_by_id(self, item_id: UUID) -> ExampleItem | None:
        """按 ID 查询单个实体。"""
        stmt = select(ExampleItemModel).where(ExampleItemModel.id == item_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def count(self) -> int:
        """返回实体总数。"""
        stmt = select(func.count()).select_from(ExampleItemModel)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_paginated(self, offset: int, limit: int) -> list[ExampleItem]:
        """分页查询实体列表，按创建时间降序排列。"""
        stmt = (
            select(ExampleItemModel)
            .order_by(ExampleItemModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]
