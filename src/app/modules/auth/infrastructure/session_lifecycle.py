"""会话生命周期管理端口实现（SPEC §12.3）。

:class:`SqlAlchemySessionLifecycle` 实现
:class:`~app.ports.session_lifecycle.SessionLifecyclePort`，
在当前 :class:`~app.ports.unit_of_work.UnitOfWork` 的事务作用域内
批量吊销会话、Access Token 和 Refresh Token。

此实现接收其他模块的 UoW（如用户模块的 ``SqlAlchemyUserUnitOfWork``），
通过 ``isinstance`` 检查确认底层是 :class:`SqlAlchemyUnitOfWork`，
然后在同一个 ``AsyncSession`` 上执行批量操作，确保用户状态变更和
会话吊销在同一事务中原子提交（SPEC §5.6）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.modules.auth.domain.model import SessionStatus
from app.modules.auth.infrastructure.models import (
    AccessTokenModel,
    RefreshTokenModel,
    SessionModel,
)
from app.ports.session_lifecycle import SessionLifecyclePort
from app.ports.unit_of_work import UnitOfWork

_logger = logging.getLogger("app.modules.auth.session_lifecycle")


class SqlAlchemySessionLifecycle(SessionLifecyclePort):
    """会话生命周期管理端口 SQLAlchemy 实现（SPEC §12.3）。

    无状态——不持有引擎或连接，所有操作通过传入 UoW 的 AsyncSession 执行。
    """

    async def revoke_all_user_sessions(
        self,
        uow: UnitOfWork,
        user_id: UUID,
        reason: str,
        current_time: datetime,
    ) -> int:
        """吊销用户全部活跃会话（SPEC §12.3）。"""
        db_session = self._get_db_session(uow)

        # 吊销活跃会话
        result = await db_session.execute(
            update(SessionModel)
            .where(
                SessionModel.user_id == user_id,
                SessionModel.status == SessionStatus.ACTIVE.value,
            )
            .values(
                status=SessionStatus.REVOKED.value,
                revoked_at=current_time,
                revoked_reason=reason,
            )
        )
        revoked_count: int = result.rowcount or 0  # type: ignore[attr-defined]

        # 删除该用户的全部 Access Token（旧 AT 立即失效）
        await db_session.execute(
            delete(AccessTokenModel).where(
                AccessTokenModel.user_id == user_id,
            )
        )

        # 吊销该用户的全部未吊销 Refresh Token
        await db_session.execute(
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.user_id == user_id,
                RefreshTokenModel.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )

        _logger.info(
            "批量吊销用户会话",
            extra={
                "event": "sessions_revoked",
                "user_id": str(user_id),
                "reason": reason,
                "count": revoked_count,
            },
        )
        return revoked_count

    async def revoke_user_sessions_except(
        self,
        uow: UnitOfWork,
        user_id: UUID,
        keep_session_id: UUID,
        reason: str,
        current_time: datetime,
    ) -> int:
        """吊销用户除指定会话外的全部活跃会话（SPEC §12.3）。"""
        db_session = self._get_db_session(uow)

        # 吊销除保留会话外的活跃会话
        result = await db_session.execute(
            update(SessionModel)
            .where(
                SessionModel.user_id == user_id,
                SessionModel.id != keep_session_id,
                SessionModel.status == SessionStatus.ACTIVE.value,
            )
            .values(
                status=SessionStatus.REVOKED.value,
                revoked_at=current_time,
                revoked_reason=reason,
            )
        )
        revoked_count: int = result.rowcount or 0  # type: ignore[attr-defined]

        # 删除保留会话外的 Access Token
        await db_session.execute(
            delete(AccessTokenModel).where(
                AccessTokenModel.user_id == user_id,
                AccessTokenModel.session_id != keep_session_id,
            )
        )

        # 吊销保留会话外的 Refresh Token
        await db_session.execute(
            update(RefreshTokenModel)
            .where(
                RefreshTokenModel.user_id == user_id,
                RefreshTokenModel.session_id != keep_session_id,
                RefreshTokenModel.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )

        _logger.info(
            "选择性吊销用户会话",
            extra={
                "event": "sessions_revoked_except",
                "user_id": str(user_id),
                "keep_session_id": str(keep_session_id),
                "reason": reason,
                "count": revoked_count,
            },
        )
        return revoked_count

    @staticmethod
    def _get_db_session(uow: UnitOfWork) -> AsyncSession:
        """从 UoW 提取底层 AsyncSession。

        其他模块的 UoW（如 ``SqlAlchemyUserUnitOfWork``）继承自
        :class:`SqlAlchemyUnitOfWork`，在激活状态下暴露 ``session`` 属性。
        """
        if not isinstance(uow, SqlAlchemyUnitOfWork):
            raise TypeError(
                f"SessionLifecyclePort 需要 SqlAlchemyUnitOfWork，实际收到 {type(uow).__name__}"
            )
        return uow.session
