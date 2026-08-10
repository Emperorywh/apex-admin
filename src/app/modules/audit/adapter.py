"""审计与登录日志 Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6 / 18.2）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收当前 UoW 的 AsyncSession，实现 ``AuditPort`` 和 ``LoginLogPort``。
审计记录与业务数据在同一事务提交（SPEC 5.7: 同提交、同回滚）。

SPEC 8.3 / 18.2: 审计日志不可变。Adapter 仅提供 INSERT 操作
（``record_audit`` / ``record_login``），不提供 UPDATE 或 DELETE 方法。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.modules.audit.orm import AuditLogORM, LoginLogORM
from app.modules.audit.port import AuditPort, LoginLogPort

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.audit.models import AuditEntry, LoginLogEntry


class SqlAlchemyAuditRepository(AuditPort):
    """SQLAlchemy 审计 Repository Adapter — 实现 ``AuditPort``.

    由 Composition Root 使用当前 UoW 的 AsyncSession 构造。
    审计记录与业务数据在同一事务提交（SPEC 5.7）。

    SPEC 8.3 / 18.2: 审计日志不可变。本 Adapter 仅提供 ``record_audit``
    （INSERT），不提供 UPDATE 或 DELETE 方法。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession.

        参数:
            session: 当前 UoW 拥有的 AsyncSession（SPEC 5.6）。
        """

        self._session = session

    async def record_audit(self, entry: AuditEntry) -> None:
        """记录操作审计条目到当前事务.

        SPEC 18.2 / 5.7: 审计记录与业务数据在同一事务提交。
        调用 ``flush`` 将记录写入事务缓冲区，但不提交（提交由最外层
        写 Use Case 通过 UoW 控制，SPEC 5.6）。

        参数:
            entry: 操作审计条目（不可变，含显示名快照和变更差异）。
        """

        orm = AuditLogORM(
            id=entry.id,
            actor_id=entry.actor_id,
            actor_display_name=entry.actor_display_name,
            module=entry.module,
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            resource_display_name=entry.resource_display_name,
            result=entry.result,
            request_id=entry.request_id,
            diff=(
                entry.diff.to_dict()
                if entry.diff is not None and not entry.diff.is_empty
                else None
            ),
            occurred_at=entry.occurred_at,
            created_at=datetime.now(UTC),
        )
        self._session.add(orm)
        await self._session.flush()

    async def count_by_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> int:
        """查询指定资源的审计记录数量 — 只读.

        SPEC 11.3: 支持删除策略——调用方检查资源是否已有审计记录。
        此方法只执行 SELECT，不修改审计数据，不违反不可变约束。
        """

        stmt = (
            select(func.count())
            .select_from(AuditLogORM)
            .where(
                AuditLogORM.resource_type == resource_type,
                AuditLogORM.resource_id == resource_id,
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)


class SqlAlchemyLoginLogRepository(LoginLogPort):
    """SQLAlchemy 登录日志 Repository Adapter — 实现 ``LoginLogPort``.

    由 Composition Root 使用当前 UoW 的 AsyncSession 构造。
    登录日志与触发操作的业务事务在同一事务提交（SPEC 5.7）。

    SPEC 8.3 / 18.1: 登录日志不可变。本 Adapter 仅提供 ``record_login``
    （INSERT），不提供 UPDATE 或 DELETE 方法。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession.

        参数:
            session: 当前 UoW 拥有的 AsyncSession（SPEC 5.6）。
        """

        self._session = session

    async def record_login(self, entry: LoginLogEntry) -> None:
        """记录登录日志到当前事务.

        SPEC 18.1: 记录用户、会话、IP、User-Agent、时间和结果。
        调用 ``flush`` 将记录写入事务缓冲区，但不提交（提交由 UoW 控制）。

        参数:
            entry: 登录日志条目（不可变，不含密码和 Token）。
        """

        orm = LoginLogORM(
            id=entry.id,
            user_id=entry.user_id,
            username=entry.username,
            session_id=entry.session_id,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            result=entry.result,
            failure_reason=entry.failure_reason,
            occurred_at=entry.occurred_at,
            created_at=datetime.now(UTC),
        )
        self._session.add(orm)
        await self._session.flush()
