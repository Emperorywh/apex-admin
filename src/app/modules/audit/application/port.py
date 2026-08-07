"""审计模块 Application Port（SPEC §5.2、§5.5、§5.6、§18.2）。

定义三种端口：

1. :class:`AuditApplicationPort` — 模块公开的应用服务接口（SPEC §5.5）。
2. :class:`AuditRepository` — 操作审计数据访问端口。
3. :class:`LoginLogRepository` — 登录日志数据访问端口。
4. :class:`AuditUnitOfWork` — 扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，
   在事务作用域内提供审计数据访问（SPEC §5.6）。

端口只定义接口，不包含运行时副作用。

审计记录不可通过普通 CRUD 修改（SPEC §18.2）：Repository 不暴露 update
或 delete 方法——审计记录为不可变追加日志。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.modules.audit.domain.model import AuditLog, LoginLog
from app.ports.unit_of_work import UnitOfWork


class AuditApplicationPort:
    """审计模块公开 Application Port（SPEC §5.5）。

    其他模块依赖此接口与审计模块协作。跨模块调用只能通过公开的
    Application Port 完成（SPEC §5.1）。

    审计模块的写入能力通过 :class:`~app.ports.audit.AuditPort` 提供
    （跨模块写入端口），本端口保留供审计查询能力（TASK-026）。
    """


class AuditRepository(ABC):
    """操作审计数据访问端口（SPEC §5.2、§18.2）。

    仅提供追加和读取操作，不提供 update 或 delete
    （SPEC §18.2：审计日志不通过普通业务 CRUD 修改）。

    所有操作在传入 UoW 的事务作用域内执行（SPEC §5.6）。
    """

    @abstractmethod
    async def add(self, entity: AuditLog) -> None:
        """追加操作审计记录到当前事务作用域。

        Args:
            entity: 待持久化的操作审计实体
        """

    @abstractmethod
    async def get_by_id(self, audit_id: UUID) -> AuditLog | None:
        """按 ID 查询操作审计记录。

        Args:
            audit_id: 审计记录 UUID

        Returns:
            匹配的实体；不存在时返回 None
        """


class LoginLogRepository(ABC):
    """登录日志数据访问端口（SPEC §5.2、§18.1）。

    仅提供追加操作——登录日志为不可变追加日志
    （SPEC §18.2：审计日志不通过普通业务 CRUD 修改）。

    所有操作在传入 UoW 的事务作用域内执行（SPEC §5.6）。
    """

    @abstractmethod
    async def add(self, entity: LoginLog) -> None:
        """追加登录日志记录到当前事务作用域。

        Args:
            entity: 待持久化的登录日志实体
        """


class AuditUnitOfWork(UnitOfWork):
    """审计模块工作单元端口（SPEC §5.6）。

    扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，在事务作用域内
    提供 :class:`AuditRepository` 和 :class:`LoginLogRepository` 访问。

    Infrastructure 层的
    :class:`~app.modules.audit.infrastructure.unit_of_work.SqlAlchemyAuditUnitOfWork`
    实现此端口。
    """

    @property
    @abstractmethod
    def audit_records(self) -> AuditRepository:
        """当前事务作用域的操作审计 Repository。"""

    @property
    @abstractmethod
    def login_logs(self) -> LoginLogRepository:
        """当前事务作用域的登录日志 Repository。"""
