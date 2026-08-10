"""认证模块 Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``SessionRepository`` 和
``LoginAttemptRepository`` Port。Adapter 在内部将 ORM 模型与领域实体互转，
确保内层不感知 ORM 类型（SPEC 5.2）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.modules.auth.models import LoginAttempt, RefreshToken, Session
from app.modules.auth.orm import LoginAttemptORM, RefreshTokenORM, SessionORM
from app.modules.auth.port import (
    LoginAttemptRepository,
    RefreshTokenRepository,
    SessionRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemySessionRepository(SessionRepository):
    """SQLAlchemy 会话 Repository Adapter — 实现 ``SessionRepository`` Port.

    由 Composition Root 使用当前 UoW 的 AsyncSession 构造（SPEC 5.6）。
    Adapter 方法返回领域实体 ``Session``，不是 ORM 模型（SPEC 5.2）。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def add(self, session: Session) -> None:
        """添加新会话到当前事务."""

        orm = _session_to_orm(session)
        self._session.add(orm)
        await self._session.flush()

    async def get_by_token_digest(self, digest: str) -> Session | None:
        """按 Access Token 摘要查找会话 — SPEC 12.2 / 12.3."""

        stmt = select(SessionORM).where(SessionORM.access_token_digest == digest)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _session_to_domain(orm) if orm else None

    async def get_by_id(self, session_id: UUID, user_id: UUID) -> Session | None:
        """按 ID 查找会话（限定用户）— 安全约束."""

        stmt = select(SessionORM).where(
            SessionORM.id == session_id,
            SessionORM.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _session_to_domain(orm) if orm else None

    async def get_by_session_id(self, session_id: UUID) -> Session | None:
        """按会话 ID 查找会话（无用户约束）— SPEC 12.2 刷新流程内部使用."""

        stmt = select(SessionORM).where(SessionORM.id == session_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _session_to_domain(orm) if orm else None

    async def list_active_by_user(self, user_id: UUID) -> list[Session]:
        """查询用户的活动会话列表."""

        stmt = (
            select(SessionORM)
            .where(
                SessionORM.user_id == user_id,
                SessionORM.revoked.is_(False),
            )
            .order_by(SessionORM.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_session_to_domain(orm) for orm in result.scalars().all()]

    async def revoke(self, session_id: UUID, *, reason: str) -> bool:
        """吊销会话."""

        stmt = (
            update(SessionORM)
            .where(
                SessionORM.id == session_id,
                SessionORM.revoked.is_(False),
            )
            .values(revoked=True, revoked_reason=reason)
        )
        result = cast("CursorResult[object]", await self._session.execute(stmt))
        await self._session.flush()
        return result.rowcount > 0

    async def revoke_all_by_user(
        self,
        user_id: UUID,
        *,
        reason: str,
    ) -> int:
        """吊销用户全部活动会话."""

        stmt = (
            update(SessionORM)
            .where(
                SessionORM.user_id == user_id,
                SessionORM.revoked.is_(False),
            )
            .values(revoked=True, revoked_reason=reason)
        )
        result = cast("CursorResult[object]", await self._session.execute(stmt))
        await self._session.flush()
        return int(result.rowcount)

    async def update_activity(
        self,
        session_id: UUID,
        *,
        last_activity_at: object,
    ) -> None:
        """更新最近活动时间."""

        stmt = (
            update(SessionORM)
            .where(SessionORM.id == session_id)
            .values(last_activity_at=last_activity_at)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def replace_access_token(
        self,
        session_id: UUID,
        *,
        new_digest: str,
        new_token_expires_at: object,
    ) -> None:
        """替换会话的 Access Token 摘要 — SPEC 12.2 刷新用.

        旧 Access Token 摘要被覆盖，立即失效。同一会话同时最多一个
        有效 Access Token（SPEC 12.2）。
        """

        stmt = (
            update(SessionORM)
            .where(SessionORM.id == session_id)
            .values(
                access_token_digest=new_digest,
                token_expires_at=new_token_expires_at,
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def revoke_all_by_user_with_session(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        reason: str,
    ) -> int:
        """在指定 session 上吊销用户全部活动会话（事务内事件处理器用）.

        SPEC 5.7: 事件处理器在当前 UoW 的 AsyncSession 上执行，
        保证与业务数据强一致。
        """

        stmt = (
            update(SessionORM)
            .where(
                SessionORM.user_id == user_id,
                SessionORM.revoked.is_(False),
            )
            .values(revoked=True, revoked_reason=reason)
        )
        result = cast("CursorResult[object]", await session.execute(stmt))
        return int(result.rowcount)


class SqlAlchemyLoginAttemptRepository(LoginAttemptRepository):
    """SQLAlchemy 登录失败计数 Adapter — 实现 ``LoginAttemptRepository`` Port.

    SPEC 12.4: 失败状态持久化到 PostgreSQL。
    使用 PostgreSQL ``INSERT ... ON CONFLICT`` 实现幂等 upsert，
    保证并发安全。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def get(self, dimension: str, key: str) -> LoginAttempt | None:
        """查询指定维度的失败计数记录."""

        stmt = select(LoginAttemptORM).where(
            LoginAttemptORM.dimension == dimension,
            LoginAttemptORM.key == key,
        )
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _attempt_to_domain(orm) if orm else None

    async def record_failure(
        self,
        dimension: str,
        key: str,
        *,
        failed_at: object,
    ) -> int:
        """记录一次失败并返回更新后的连续失败次数.

        使用 PostgreSQL ``INSERT ... ON CONFLICT`` 实现原子 upsert:
        记录不存在时插入（count=1），存在时递增 count。
        仅递增计数和 ``last_failed_at``，不设置锁定。
        """

        stmt = (
            pg_insert(LoginAttemptORM)
            .values(
                id=uuid4(),
                dimension=dimension,
                key=key,
                failed_count=1,
                last_failed_at=failed_at,
                locked_until=None,
            )
            .on_conflict_do_update(
                index_elements=["dimension", "key"],
                set_={
                    "failed_count": LoginAttemptORM.failed_count + 1,
                    "last_failed_at": failed_at,
                },
            )
            .returning(LoginAttemptORM.failed_count)
        )
        result = await self._session.execute(stmt)
        count = result.scalar() or 0
        await self._session.flush()
        return int(count)

    async def lock(
        self,
        dimension: str,
        key: str,
        *,
        locked_until: object,
    ) -> None:
        """设置指定维度的锁定截止时间."""

        stmt = (
            update(LoginAttemptORM)
            .where(
                LoginAttemptORM.dimension == dimension,
                LoginAttemptORM.key == key,
            )
            .values(locked_until=locked_until)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def reset(self, dimension: str, key: str) -> None:
        """重置指定维度的失败计数.

        SPEC 12.4: "成功登录后清理该账号失败状态"。
        直接删除记录，下次失败时重新创建。
        """

        stmt = (
            update(LoginAttemptORM)
            .where(
                LoginAttemptORM.dimension == dimension,
                LoginAttemptORM.key == key,
            )
            .values(failed_count=0, last_failed_at=None, locked_until=None)
        )
        await self._session.execute(stmt)
        await self._session.flush()


class SqlAlchemyRefreshTokenRepository(RefreshTokenRepository):
    """SQLAlchemy Refresh Token Repository Adapter.

    实现 ``RefreshTokenRepository`` Port（SPEC 12.2）。

    SPEC 12.2:
      - Token 轮换在同一事务中完成新旧状态变更。
      - 刷新事务对 Token Family 加行锁（``SELECT ... FOR UPDATE``）。
      - 并发请求只允许一个成功。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def add(self, token: RefreshToken) -> None:
        """添加新 Refresh Token 到当前事务."""

        orm = _refresh_token_to_orm(token)
        self._session.add(orm)
        await self._session.flush()

    async def get_by_digest(self, digest: str) -> RefreshToken | None:
        """按 HMAC 摘要查找 Refresh Token（不加锁）."""

        stmt = select(RefreshTokenORM).where(RefreshTokenORM.token_digest == digest)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _refresh_token_to_domain(orm) if orm else None

    async def lock_family(self, family_id: UUID) -> None:
        """对 Token Family 加行锁 — SPEC 12.2.

        对 Family 中所有行执行 ``SELECT ... FOR UPDATE``。锁在事务提交
        或回滚后释放，保证并发刷新请求串行化。
        """

        stmt = (
            select(RefreshTokenORM)
            .where(RefreshTokenORM.family_id == family_id)
            .with_for_update()
        )
        await self._session.execute(stmt)

    async def mark_used(
        self,
        token_id: UUID,
        *,
        used_at: object,
    ) -> None:
        """标记 Refresh Token 为已使用."""

        stmt = (
            update(RefreshTokenORM)
            .where(RefreshTokenORM.id == token_id)
            .values(used_at=used_at)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def revoke_family(
        self,
        family_id: UUID,
        *,
        reason: str,
    ) -> None:
        """吊销整个 Token Family — SPEC 12.2 重放检测."""

        stmt = (
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.family_id == family_id,
                RefreshTokenORM.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def revoke_by_session(
        self,
        session_id: UUID,
        *,
        reason: str,
    ) -> None:
        """吊销会话关联的全部 Refresh Token."""

        stmt = (
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.session_id == session_id,
                RefreshTokenORM.revoked_reason.is_(None),
            )
            .values(revoked_reason=reason)
        )
        await self._session.execute(stmt)
        await self._session.flush()


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _session_to_domain(orm: SessionORM) -> Session:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return Session(
        id=orm.id,
        user_id=orm.user_id,
        access_token_digest=orm.access_token_digest,
        device=orm.device,
        ip_address=orm.ip_address,
        user_agent=orm.user_agent,
        created_at=orm.created_at,
        last_activity_at=orm.last_activity_at,
        absolute_expires_at=orm.absolute_expires_at,
        token_expires_at=orm.token_expires_at,
        revoked=orm.revoked,
        revoked_reason=orm.revoked_reason,
    )


def _session_to_orm(session: Session) -> SessionORM:
    """领域实体 → ORM 模型转换."""

    return SessionORM(
        id=session.id,
        user_id=session.user_id,
        access_token_digest=session.access_token_digest,
        device=session.device,
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
        absolute_expires_at=session.absolute_expires_at,
        token_expires_at=session.token_expires_at,
        revoked=session.revoked,
        revoked_reason=session.revoked_reason,
    )


def _attempt_to_domain(orm: LoginAttemptORM) -> LoginAttempt:
    """ORM 模型 → 领域实体转换."""

    return LoginAttempt(
        id=orm.id,
        dimension=orm.dimension,
        key=orm.key,
        failed_count=orm.failed_count,
        last_failed_at=orm.last_failed_at,
        locked_until=orm.locked_until,
    )


def _refresh_token_to_domain(orm: RefreshTokenORM) -> RefreshToken:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return RefreshToken(
        id=orm.id,
        session_id=orm.session_id,
        family_id=orm.family_id,
        token_digest=orm.token_digest,
        predecessor_id=orm.predecessor_id,
        created_at=orm.created_at,
        used_at=orm.used_at,
        expires_at=orm.expires_at,
        revoked_reason=orm.revoked_reason,
    )


def _refresh_token_to_orm(token: RefreshToken) -> RefreshTokenORM:
    """领域实体 → ORM 模型转换."""

    return RefreshTokenORM(
        id=token.id,
        session_id=token.session_id,
        family_id=token.family_id,
        token_digest=token.token_digest,
        predecessor_id=token.predecessor_id,
        created_at=token.created_at,
        used_at=token.used_at,
        expires_at=token.expires_at,
        revoked_reason=token.revoked_reason,
    )
