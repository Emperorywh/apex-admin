"""审计查询与保留治理 Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6 / 18.3 / 18.4）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收当前 UoW 的 AsyncSession，实现:
  - ``AuditQueryPort`` — 审计日志只读分页查询与详情。
  - ``LoginLogQueryPort`` — 登录日志只读分页查询与详情。
  - ``AuditRetentionPort`` — 过期记录计数与受控删除。

查询方法为只读 SELECT，不修改审计数据（SPEC 8.3: 不可变约束）。
删除方法仅在受控管理命令中调用（SPEC 18.4: 受控清理命令）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from app.modules.audit.models import AuditEntry, LoginLogEntry
from app.modules.audit.orm import AuditLogORM, LoginLogORM
from app.modules.audit.query_port import (
    AuditLogFilters,
    AuditQueryPort,
    AuditRetentionPort,
    LoginLogFilters,
    LoginLogQueryPort,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyAuditQueryAdapter(AuditQueryPort):
    """SQLAlchemy 审计日志查询 Adapter — 实现 ``AuditQueryPort``.

    由 Composition Root 使用当前 UoW 的 AsyncSession 构造。
    所有方法均为只读 SELECT，不修改审计数据。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession.

        参数:
            session: 当前 UoW 拥有的 AsyncSession（SPEC 5.6）。
        """

        self._session = session

    async def query_audit_logs(
        self,
        filters: AuditLogFilters,
        offset: int,
        limit: int,
    ) -> tuple[list[AuditEntry], int]:
        """分页查询审计日志 — SPEC 18.3.

        按操作者/模块/动作/资源/结果/时间范围筛选，
        按 ``occurred_at`` 降序排列（最新优先）。
        """

        conditions = _build_audit_filters(filters)
        base = select(AuditLogORM)
        for cond in conditions:
            base = base.where(cond)

        # 总数
        count_stmt = select(func.count()).select_from(base.subquery())
        total_result = await self._session.execute(count_stmt)
        total = int(total_result.scalar() or 0)

        # 分页数据 — 按 occurred_at 降序
        data_stmt = (
            base.order_by(AuditLogORM.occurred_at.desc()).offset(offset).limit(limit)
        )
        data_result = await self._session.execute(data_stmt)
        rows = data_result.scalars().all()
        items = [_orm_to_audit_entry(orm) for orm in rows]
        return items, total

    async def get_audit_log_by_id(
        self,
        log_id: UUID,
    ) -> AuditEntry | None:
        """按 ID 查询单条审计日志 — SPEC 18.3."""

        stmt = select(AuditLogORM).where(AuditLogORM.id == log_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return _orm_to_audit_entry(orm)


class SqlAlchemyLoginLogQueryAdapter(LoginLogQueryPort):
    """SQLAlchemy 登录日志查询 Adapter — 实现 ``LoginLogQueryPort``.

    由 Composition Root 使用当前 UoW 的 AsyncSession 构造。
    所有方法均为只读 SELECT，不修改审计数据。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def query_login_logs(
        self,
        filters: LoginLogFilters,
        offset: int,
        limit: int,
    ) -> tuple[list[LoginLogEntry], int]:
        """分页查询登录日志 — SPEC 18.1 / 18.3.

        按用户/IP/结果/时间范围筛选，
        按 ``occurred_at`` 降序排列（最新优先）。
        """

        conditions = _build_login_filters(filters)
        base = select(LoginLogORM)
        for cond in conditions:
            base = base.where(cond)

        # 总数
        count_stmt = select(func.count()).select_from(base.subquery())
        total_result = await self._session.execute(count_stmt)
        total = int(total_result.scalar() or 0)

        # 分页数据
        data_stmt = (
            base.order_by(LoginLogORM.occurred_at.desc()).offset(offset).limit(limit)
        )
        data_result = await self._session.execute(data_stmt)
        rows = data_result.scalars().all()
        items = [_orm_to_login_entry(orm) for orm in rows]
        return items, total

    async def get_login_log_by_id(
        self,
        log_id: UUID,
    ) -> LoginLogEntry | None:
        """按 ID 查询单条登录日志 — SPEC 18.3."""

        stmt = select(LoginLogORM).where(LoginLogORM.id == log_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return _orm_to_login_entry(orm)


class SqlAlchemyAuditRetentionAdapter(AuditRetentionPort):
    """SQLAlchemy 审计保留治理 Adapter — 实现 ``AuditRetentionPort``.

    SPEC 18.4: 提供受控的归档或清理命令。
    删除方法仅在管理命令中调用，不存在于普通业务 CRUD 路径。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def count_expired_audit_logs(self, cutoff: datetime) -> int:
        """统计过期的审计日志数量 — 只读."""

        stmt = (
            select(func.count())
            .select_from(AuditLogORM)
            .where(AuditLogORM.occurred_at < cutoff)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def delete_expired_audit_logs(self, cutoff: datetime) -> int:
        """删除过期的审计日志 — 受控操作（SPEC 18.4 / 25.3）.

        仅在管理命令 ``audit cleanup --apply`` 中调用。
        返回已删除的行数。
        """

        # 先计数，再删除
        count = await self.count_expired_audit_logs(cutoff)
        if count == 0:
            return 0
        stmt = delete(AuditLogORM).where(AuditLogORM.occurred_at < cutoff)
        await self._session.execute(stmt)
        return count

    async def count_expired_login_logs(self, cutoff: datetime) -> int:
        """统计过期的登录日志数量 — 只读."""

        stmt = (
            select(func.count())
            .select_from(LoginLogORM)
            .where(LoginLogORM.occurred_at < cutoff)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def delete_expired_login_logs(self, cutoff: datetime) -> int:
        """删除过期的登录日志 — 受控操作（SPEC 18.4 / 25.3）."""

        count = await self.count_expired_login_logs(cutoff)
        if count == 0:
            return 0
        stmt = delete(LoginLogORM).where(LoginLogORM.occurred_at < cutoff)
        await self._session.execute(stmt)
        return count


# ── 内部辅助 ─────────────────────────────────────────────────────────────


def _build_audit_filters(filters: AuditLogFilters) -> list[Any]:
    """构建审计日志查询条件列表."""

    conditions: list[Any] = []
    if filters.actor_id is not None:
        conditions.append(AuditLogORM.actor_id == filters.actor_id)
    if filters.module is not None:
        conditions.append(AuditLogORM.module == filters.module)
    if filters.action is not None:
        conditions.append(AuditLogORM.action == filters.action)
    if filters.resource_type is not None:
        conditions.append(AuditLogORM.resource_type == filters.resource_type)
    if filters.resource_id is not None:
        conditions.append(AuditLogORM.resource_id == filters.resource_id)
    if filters.result is not None:
        conditions.append(AuditLogORM.result == filters.result)
    if filters.start_time is not None:
        conditions.append(AuditLogORM.occurred_at >= filters.start_time)
    if filters.end_time is not None:
        conditions.append(AuditLogORM.occurred_at <= filters.end_time)
    return conditions


def _build_login_filters(filters: LoginLogFilters) -> list[Any]:
    """构建登录日志查询条件列表."""

    conditions: list[Any] = []
    if filters.user_id is not None:
        conditions.append(LoginLogORM.user_id == filters.user_id)
    if filters.username is not None:
        conditions.append(LoginLogORM.username == filters.username)
    if filters.ip_address is not None:
        conditions.append(LoginLogORM.ip_address == filters.ip_address)
    if filters.result is not None:
        conditions.append(LoginLogORM.result == filters.result)
    if filters.start_time is not None:
        conditions.append(LoginLogORM.occurred_at >= filters.start_time)
    if filters.end_time is not None:
        conditions.append(LoginLogORM.occurred_at <= filters.end_time)
    return conditions


def _orm_to_audit_entry(orm: AuditLogORM) -> AuditEntry:
    """ORM → 领域实体转换."""

    from app.modules.audit.models import ChangeDiff, DiffField

    diff: ChangeDiff | None = None
    if orm.diff is not None:
        fields = tuple(
            DiffField(
                field_name=name,
                old_value=val.get("old") if isinstance(val, dict) else None,
                new_value=val.get("new") if isinstance(val, dict) else None,
            )
            for name, val in sorted(orm.diff.items())
        )
        diff = ChangeDiff(fields=fields)

    return AuditEntry(
        id=orm.id,
        actor_id=orm.actor_id,
        actor_display_name=orm.actor_display_name,
        module=orm.module,
        action=orm.action,
        resource_type=orm.resource_type,
        resource_id=orm.resource_id,
        resource_display_name=orm.resource_display_name,
        result=orm.result,
        request_id=orm.request_id,
        diff=diff,
        occurred_at=orm.occurred_at,
    )


def _orm_to_login_entry(orm: LoginLogORM) -> LoginLogEntry:
    """ORM → 领域实体转换."""

    return LoginLogEntry(
        id=orm.id,
        user_id=orm.user_id,
        username=orm.username,
        session_id=orm.session_id,
        ip_address=orm.ip_address,
        user_agent=orm.user_agent,
        result=orm.result,
        failure_reason=orm.failure_reason,
        occurred_at=orm.occurred_at,
    )
