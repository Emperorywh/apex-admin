"""认证模块 Repository Adapter（SPEC §5.2）。

实现 :class:`~app.modules.auth.application.port` 中定义的三个 Repository 端口，
使用 SQLAlchemy AsyncSession 执行数据访问。Repository 不自行提交或回滚，
所有操作在传入的 Session（由 UoW 管理）的事务作用域内执行（SPEC §5.6）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.application.port import (
    AccessTokenRepository,
    RefreshTokenRepository,
    SessionRepository,
)
from app.modules.auth.domain.model import (
    AccessTokenRecord,
    RefreshTokenRecord,
    Session,
)
from app.modules.auth.infrastructure.models import (
    AccessTokenModel,
    RefreshTokenModel,
    SessionModel,
)


class SqlAlchemySessionRepository(SessionRepository):
    """基于 SQLAlchemy 的会话 Repository。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: Session) -> None:
        """添加会话实体到当前 Session。"""
        model = SessionModel.from_entity(entity)
        self._session.add(model)

    async def get_by_id(self, session_id: UUID) -> Session | None:
        """按 ID 查询会话。"""
        stmt = select(SessionModel).where(SessionModel.id == session_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        """查询用户的活动会话列表，按创建时间降序排列。"""
        stmt = (
            select(SessionModel)
            .where(SessionModel.user_id == user_id)
            .order_by(SessionModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]

    async def update(self, entity: Session) -> None:
        """更新会话实体。"""
        model = SessionModel.from_entity(entity)
        await self._session.merge(model)


class SqlAlchemyAccessTokenRepository(AccessTokenRepository):
    """基于 SQLAlchemy 的 Access Token Repository。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: AccessTokenRecord) -> None:
        """添加 Access Token 摘要记录到当前 Session。"""
        model = AccessTokenModel.from_entity(entity)
        self._session.add(model)

    async def get_by_digest(self, digest: str) -> AccessTokenRecord | None:
        """按 HMAC 摘要查询 Access Token 记录。"""
        stmt = select(AccessTokenModel).where(AccessTokenModel.digest == digest)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def delete_by_session(self, session_id: UUID) -> None:
        """删除指定会话的全部 Access Token 记录。"""
        stmt = delete(AccessTokenModel).where(
            AccessTokenModel.session_id == session_id,
        )
        await self._session.execute(stmt)


class SqlAlchemyRefreshTokenRepository(RefreshTokenRepository):
    """基于 SQLAlchemy 的 Refresh Token Repository。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: RefreshTokenRecord) -> None:
        """添加 Refresh Token 摘要记录到当前 Session。"""
        model = RefreshTokenModel.from_entity(entity)
        self._session.add(model)

    async def get_by_digest(self, digest: str) -> RefreshTokenRecord | None:
        """按 HMAC 摘要查询 Refresh Token 记录。"""
        stmt = select(RefreshTokenModel).where(RefreshTokenModel.digest == digest)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None
