"""认证模块 Repository Adapter（SPEC §5.2）。

实现 :class:`~app.modules.auth.application.port` 中定义的三个 Repository 端口，
使用 SQLAlchemy AsyncSession 执行数据访问。Repository 不自行提交或回滚，
所有操作在传入的 Session（由 UoW 管理）的事务作用域内执行（SPEC §5.6）。

TASK-016 新增方法：
- ``get_by_digest_for_update`` — 对 Refresh Token 行加 ``FOR UPDATE`` 锁
- ``revoke_by_family`` / ``revoke_by_session`` / ``revoke_by_user`` — 批量吊销
- ``delete_by_user`` — 批量删除 Access Token
- ``get_by_id_for_update`` — 对 Session 行加锁
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select, update
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
    SessionStatus,
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

    async def get_by_id_for_update(self, session_id: UUID) -> Session | None:
        """按 ID 查询会话并加行锁（SPEC §12.2）。"""
        stmt = select(SessionModel).where(SessionModel.id == session_id).with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        """查询用户的活动会话列表，按创建时间降序排列。"""
        stmt = (
            select(SessionModel)
            .where(
                SessionModel.user_id == user_id,
                SessionModel.status == SessionStatus.ACTIVE.value,
            )
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

    async def delete_by_user(self, user_id: UUID) -> None:
        """删除指定用户的全部 Access Token 记录。"""
        stmt = delete(AccessTokenModel).where(
            AccessTokenModel.user_id == user_id,
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

    async def get_by_digest_for_update(
        self,
        digest: str,
    ) -> RefreshTokenRecord | None:
        """按 HMAC 摘要查询 Refresh Token 记录并加行锁（SPEC §12.2）。

        使用 ``SELECT ... FOR UPDATE`` 锁定 Token 行，确保并发刷新串行化。
        同一 Refresh Token 的并发请求中，仅第一个能读取到未使用状态并
        完成轮换；后续请求读取到已使用状态并触发重放检测。
        """
        stmt = select(RefreshTokenModel).where(RefreshTokenModel.digest == digest).with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def update(self, entity: RefreshTokenRecord) -> None:
        """更新 Refresh Token 记录（标记已使用或吊销）。"""
        model = RefreshTokenModel.from_entity(entity)
        await self._session.merge(model)

    async def revoke_by_family(
        self,
        token_family_id: UUID,
        reason: str,
    ) -> int:
        """吊销 Token Family 中全部未吊销记录（SPEC §12.2：重放检测）。"""
        stmt = (
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.token_family_id == token_family_id,
                RefreshTokenModel.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def revoke_by_session(
        self,
        session_id: UUID,
        reason: str,
    ) -> int:
        """吊销指定会话的全部未吊销 Refresh Token。"""
        stmt = (
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.session_id == session_id,
                RefreshTokenModel.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def revoke_by_user(
        self,
        user_id: UUID,
        reason: str,
    ) -> int:
        """吊销指定用户的全部未吊销 Refresh Token。"""
        stmt = (
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.user_id == user_id,
                RefreshTokenModel.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def revoke_by_user_except(
        self,
        user_id: UUID,
        keep_session_id: UUID,
        reason: str,
    ) -> int:
        """吊销指定用户除某会话外的全部未吊销 Refresh Token。"""
        stmt = (
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.user_id == user_id,
                RefreshTokenModel.session_id != keep_session_id,
                RefreshTokenModel.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )
        result = await self._session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined, no-any-return]
