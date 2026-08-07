"""登录失败记录 Repository Adapter（SPEC §5.2、§12.4）。

实现 :class:`~app.modules.auth.application.port.LoginAttemptRepository` 端口，
使用 SQLAlchemy AsyncSession 执行数据访问。暴力破解防护的失败计数
持久化到 PostgreSQL，支持跨多 Worker 工作（SPEC §12.4）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.application.port import LoginAttemptRepository
from app.modules.auth.domain.login_security import LoginAttempt, LoginAttemptDimension
from app.modules.auth.infrastructure.models import LoginAttemptModel


class SqlAlchemyLoginAttemptRepository(LoginAttemptRepository):
    """基于 SQLAlchemy 的登录失败记录 Repository。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        dimension: LoginAttemptDimension,
        identifier: str,
    ) -> LoginAttempt | None:
        """按维度和标识符查询登录失败记录。"""
        stmt = select(LoginAttemptModel).where(
            LoginAttemptModel.dimension == dimension.value,
            LoginAttemptModel.identifier == identifier,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def get_for_update(
        self,
        dimension: LoginAttemptDimension,
        identifier: str,
    ) -> LoginAttempt | None:
        """按维度和标识符查询并加行锁（SPEC §12.4）。

        使用 ``SELECT ... FOR UPDATE`` 锁定行，确保并发失败记录串行化，
        避免计数竞争（跨多 Worker 场景，SPEC §12.4）。
        """
        stmt = (
            select(LoginAttemptModel)
            .where(
                LoginAttemptModel.dimension == dimension.value,
                LoginAttemptModel.identifier == identifier,
            )
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None

    async def save(self, entity: LoginAttempt) -> None:
        """插入或更新登录失败记录（upsert 语义）。

        使用 PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE`` 实现
        原子 upsert，在 ``get_for_update`` 之后调用时由行锁保证
        并发安全（SPEC §12.4）。
        """
        stmt = pg_insert(LoginAttemptModel).values(
            dimension=entity.dimension.value,
            identifier=entity.identifier,
            failure_count=entity.failure_count,
            locked_until=entity.locked_until,
            last_failure_at=entity.last_failure_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["dimension", "identifier"],
            set_=dict(  # noqa: C408
                failure_count=stmt.excluded.failure_count,
                locked_until=stmt.excluded.locked_until,
                last_failure_at=stmt.excluded.last_failure_at,
            ),
        )
        await self._session.execute(stmt)

    async def delete(
        self,
        dimension: LoginAttemptDimension,
        identifier: str,
    ) -> None:
        """删除登录失败记录（清理该维度失败状态）。"""
        from sqlalchemy import delete as sa_delete

        stmt = sa_delete(LoginAttemptModel).where(
            LoginAttemptModel.dimension == dimension.value,
            LoginAttemptModel.identifier == identifier,
        )
        await self._session.execute(stmt)
