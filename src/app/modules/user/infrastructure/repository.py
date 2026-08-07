"""用户模块 Repository Adapter（SPEC §5.2）。

实现 :class:`~app.modules.user.application.port.UserRepository` 端口，
使用 SQLAlchemy AsyncSession 执行数据访问。Repository 不自行提交或回滚，
所有操作在传入的 Session（由 UoW 管理）的事务作用域内执行（SPEC §5.6）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.application.port import UserRepository
from app.modules.user.domain.model import User
from app.modules.user.infrastructure.models import UserModel


class SqlAlchemyUserRepository(UserRepository):
    """基于 SQLAlchemy 的用户 Repository。

    每个实例在构造时接收一个 :class:`~sqlalchemy.ext.asyncio.AsyncSession`，
    该 Session 由上层 Unit of Work 创建和管理。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: User) -> None:
        """将用户实体添加到当前 Session。"""
        model = UserModel.from_entity(entity)
        self._session.add(model)

    async def get_by_id(self, user_id: UUID) -> User | None:
        """按 ID 查询单个用户。"""
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def get_by_username(self, username: str) -> User | None:
        """按用户名查询单个用户（用于唯一性检查）。"""
        stmt = select(UserModel).where(UserModel.username == username)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def count(self) -> int:
        """返回用户总数。"""
        stmt = select(func.count()).select_from(UserModel)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_paginated(
        self,
        offset: int,
        limit: int,
    ) -> list[User]:
        """分页查询用户列表，按创建时间降序排列。"""
        stmt = select(UserModel).order_by(UserModel.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]

    async def update(self, entity: User) -> None:
        """更新用户实体。

        使用 ``merge`` 将实体的变更同步到当前 Session。
        """
        model = UserModel.from_entity(entity)
        await self._session.merge(model)
