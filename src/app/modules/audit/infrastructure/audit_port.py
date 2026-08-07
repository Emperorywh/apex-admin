"""审计端口实现（SPEC §5.7、§18.2）。

:class:`SqlAlchemyAuditPort` 实现 :class:`~app.ports.audit.AuditPort`，
在当前 :class:`~app.ports.unit_of_work.UnitOfWork` 的事务作用域内
记录操作审计。

此实现接收其他模块的 UoW（如用户模块的 ``SqlAlchemyUserUnitOfWork``），
通过 ``isinstance`` 检查确认底层是 :class:`SqlAlchemyUnitOfWork`，
然后在同一个 ``AsyncSession`` 上追加审计记录，确保审计记录与业务数据
在同一事务中原子提交（SPEC §5.6、§18.2）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.modules.audit.domain.model import AuditLog
from app.modules.audit.infrastructure.repository import SqlAlchemyAuditRepository
from app.ports.audit import AuditDiff, AuditPort, AuditResult
from app.ports.unit_of_work import UnitOfWork


class SqlAlchemyAuditPort(AuditPort):
    """审计端口 SQLAlchemy 实现（SPEC §5.7、§18.2）。

    无状态——不持有引擎或连接，所有操作通过传入 UoW 的 AsyncSession 执行。
    审计记录追加到当前事务，与业务数据在同一事务提交（SPEC §18.2）。
    """

    async def record(  # noqa: PLR0913
        self,
        uow: UnitOfWork,
        *,
        actor_id: UUID | None,
        actor_display_name: str | None,
        occurred_at: datetime,
        module: str,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        resource_display_name: str | None = None,
        result: AuditResult,
        request_id: str | None = None,
        diff: AuditDiff | None = None,
    ) -> None:
        """在当前事务内记录操作审计（SPEC §5.7、§18.2）。

        创建 :class:`AuditLog` 实体并追加到当前 UoW 的 AsyncSession。
        审计记录与业务数据在同一事务提交（SPEC §18.2）。
        """
        session = self._get_db_session(uow)
        entity = AuditLog.new(
            actor_id=actor_id,
            actor_display_name=actor_display_name,
            occurred_at=occurred_at,
            module=module,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result=result,
            request_id=request_id,
            diff=diff,
        )
        repo = SqlAlchemyAuditRepository(session)
        await repo.add(entity)

    @staticmethod
    def _get_db_session(uow: UnitOfWork) -> AsyncSession:
        """从 UoW 提取底层 AsyncSession。

        其他模块的 UoW 继承自 :class:`SqlAlchemyUnitOfWork`，
        在激活状态下暴露 ``session`` 属性。
        """
        if not isinstance(uow, SqlAlchemyUnitOfWork):
            raise TypeError(f"AuditPort 需要 SqlAlchemyUnitOfWork，实际收到 {type(uow).__name__}")
        return uow.session
