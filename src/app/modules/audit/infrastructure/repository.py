"""审计模块 Repository Adapter（SPEC §5.2）。

实现 :class:`~app.modules.audit.application.port.AuditRepository` 和
:class:`~app.modules.audit.application.port.LoginLogRepository` 端口，
使用 SQLAlchemy AsyncSession 执行数据访问。

Repository 不自行提交或回滚，所有操作在传入 Session（由 UoW 管理）
的事务作用域内执行（SPEC §5.6）。

审计记录为不可变追加日志——Repository 仅提供 ``add`` 和只读查询方法，
不提供 ``update`` 或 ``delete``（SPEC §18.2：审计日志不通过普通业务 CRUD
修改）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.application.port import AuditRepository, LoginLogRepository
from app.modules.audit.domain.model import AuditLog, LoginLog
from app.modules.audit.infrastructure.models import AuditLogModel, LoginLogModel


class SqlAlchemyAuditRepository(AuditRepository):
    """基于 SQLAlchemy 的操作审计 Repository。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: AuditLog) -> None:
        """追加操作审计记录到当前 Session。"""
        model = AuditLogModel.from_entity(entity)
        self._session.add(model)

    async def get_by_id(self, audit_id: UUID) -> AuditLog | None:
        """按 ID 查询操作审计记录。"""
        stmt = select(AuditLogModel).where(AuditLogModel.id == audit_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model is not None else None


class SqlAlchemyLoginLogRepository(LoginLogRepository):
    """基于 SQLAlchemy 的登录日志 Repository。

    Args:
        session: 当前事务作用域的 AsyncSession
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entity: LoginLog) -> None:
        """追加登录日志记录到当前 Session。"""
        model = LoginLogModel.from_entity(entity)
        self._session.add(model)
