"""审计模块工作单元实现（SPEC §5.6）。

继承 :class:`~app.infrastructure.database.unit_of_work.SqlAlchemyUnitOfWork`
（提供 ``__aenter__``/``__aexit__``/``commit``/``rollback`` 和 ``session`` 属性），
同时实现 :class:`~app.modules.audit.application.port.AuditUnitOfWork`
（提供 ``audit_records`` 和 ``login_logs`` Repository 访问）。
"""

from __future__ import annotations

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.modules.audit.application.port import AuditUnitOfWork
from app.modules.audit.infrastructure.repository import (
    SqlAlchemyAuditRepository,
    SqlAlchemyLoginLogRepository,
)


class SqlAlchemyAuditUnitOfWork(SqlAlchemyUnitOfWork, AuditUnitOfWork):
    """审计模块 SQLAlchemy 工作单元。

    在基类事务管理的基础上，通过属性提供审计模块的 Repository 访问。
    Repository 每次访问时从当前 Session 构造，不缓存状态。
    """

    @property
    def audit_records(self) -> SqlAlchemyAuditRepository:
        """当前事务作用域的操作审计 Repository。"""
        return SqlAlchemyAuditRepository(self.session)

    @property
    def login_logs(self) -> SqlAlchemyLoginLogRepository:
        """当前事务作用域的登录日志 Repository。"""
        return SqlAlchemyLoginLogRepository(self.session)
