"""审计查询与导出 Use Case — Application 层应用服务.

SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。
SPEC 18.3: 审计日志查询本身受到权限控制。
SPEC 18.3: 审计日志导出由审计模块以流式文件下载自行实现，
不依赖 22.1 的通用导出扩展；导出属于受控操作并记录新的审计事件。
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

from app.modules.audit.query_adapter import (
    SqlAlchemyAuditQueryAdapter,
    SqlAlchemyLoginLogQueryAdapter,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.context import UseCaseContext
    from app.application.ports import Clock, IdGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.models import AuditEntry, LoginLogEntry
    from app.modules.audit.port import AuditPort
    from app.modules.audit.query_port import AuditLogFilters, LoginLogFilters

# ── 导出 CSV 列定义 ──────────────────────────────────────────────────────

#: 审计日志 CSV 导出列头。
AUDIT_LOG_CSV_COLUMNS = (
    "id",
    "actor_id",
    "actor_display_name",
    "module",
    "action",
    "resource_type",
    "resource_id",
    "resource_display_name",
    "result",
    "request_id",
    "occurred_at",
)

#: 登录日志 CSV 导出列头。
LOGIN_LOG_CSV_COLUMNS = (
    "id",
    "user_id",
    "username",
    "session_id",
    "ip_address",
    "user_agent",
    "result",
    "failure_reason",
    "occurred_at",
)

#: 流式导出每批次查询的记录数。
_EXPORT_BATCH_SIZE = 500


class AuditQueryUseCase:
    """审计查询与导出 Use Case — Application 层应用服务.

    SPEC 18.3: 提供审计日志与登录日志的分页筛选查询、详情查看和流式导出。

    构造参数:
        uow_factory:   UoW 工厂。
        clock:         时钟 Port。
        id_generator:  标识生成器 Port。
        audit_factory: 审计写入 Port 工厂（用于记录导出操作本身的审计事件）。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        audit_factory: Callable[[AsyncSession], AuditPort],
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._audit_factory = audit_factory

    def _create_audit_query(self, session: AsyncSession) -> SqlAlchemyAuditQueryAdapter:
        """从 session 构造审计查询 Adapter — SPEC 5.6."""

        return SqlAlchemyAuditQueryAdapter(session)

    def _create_login_query(
        self,
        session: AsyncSession,
    ) -> SqlAlchemyLoginLogQueryAdapter:
        """从 session 构造登录日志查询 Adapter — SPEC 5.6."""

        return SqlAlchemyLoginLogQueryAdapter(session)

    def _create_audit(self, session: AsyncSession) -> AuditPort:
        """从 session 构造审计写入 Port — SPEC 5.7 / 5.2."""

        return self._audit_factory(session)

    # ════════════════════════════════════════════════════════════════════════
    # 审计日志查询 — SPEC 18.3
    # ════════════════════════════════════════════════════════════════════════

    async def query_audit_logs(
        self,
        ctx: UseCaseContext,
        filters: AuditLogFilters,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, object]], int]:
        """分页查询审计日志 — SPEC 18.3.

        SPEC 18.3: 按操作者/模块/动作/资源/结果/时间范围筛选。
        查询本身受到权限控制（由 Router 层权限依赖保证）。
        """

        del ctx  # 权限检查在 Router 层完成，Use Case 直接查询

        async with self._uow_factory() as uow:
            query = self._create_audit_query(uow.session)
            items, total = await query.query_audit_logs(filters, offset, limit)
            return [_audit_entry_to_response(e) for e in items], total

    async def get_audit_log(
        self,
        ctx: UseCaseContext,
        log_id: UUID,
    ) -> dict[str, object]:
        """查询单条审计日志详情 — SPEC 18.3."""

        del ctx

        async with self._uow_factory() as uow:
            query = self._create_audit_query(uow.session)
            entry = await query.get_audit_log_by_id(log_id)
            if entry is None:
                from app.modules.audit.errors import AuditLogNotFoundError

                raise AuditLogNotFoundError(str(log_id))
            return _audit_entry_to_response(entry)

    # ════════════════════════════════════════════════════════════════════════
    # 登录日志查询 — SPEC 18.1 / 18.3
    # ════════════════════════════════════════════════════════════════════════

    async def query_login_logs(
        self,
        ctx: UseCaseContext,
        filters: LoginLogFilters,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, object]], int]:
        """分页查询登录日志 — SPEC 18.1 / 18.3."""

        del ctx

        async with self._uow_factory() as uow:
            query = self._create_login_query(uow.session)
            items, total = await query.query_login_logs(filters, offset, limit)
            return [_login_entry_to_response(e) for e in items], total

    async def get_login_log(
        self,
        ctx: UseCaseContext,
        log_id: UUID,
    ) -> dict[str, object]:
        """查询单条登录日志详情 — SPEC 18.3."""

        del ctx

        async with self._uow_factory() as uow:
            query = self._create_login_query(uow.session)
            entry = await query.get_login_log_by_id(log_id)
            if entry is None:
                from app.modules.audit.errors import LoginLogNotFoundError

                raise LoginLogNotFoundError(str(log_id))
            return _login_entry_to_response(entry)

    # ════════════════════════════════════════════════════════════════════════
    # 流式导出 — SPEC 18.3
    # ════════════════════════════════════════════════════════════════════════

    async def export_audit_logs(
        self,
        ctx: UseCaseContext,
        filters: AuditLogFilters,
    ) -> AsyncIterator[bytes]:
        """流式导出审计日志为 CSV — SPEC 18.3.

        SPEC 18.3: 审计日志导出由审计模块以流式文件下载自行实现，
        不依赖 22.1 的通用导出扩展；导出属于受控操作并记录新的审计事件。

        先记录导出审计事件（独立事务提交），再流式查询输出 CSV。
        分批查询避免一次加载全部数据到内存。
        """

        # 1. 记录导出操作的审计事件（SPEC 18.3: 导出行为本身写入新的审计事件）
        await self._record_export_audit_event(
            ctx,
            action="audit.log.export",
            resource_type="audit_log",
            filter_summary=_describe_audit_filters(filters),
        )

        # 2. 流式输出 CSV
        async for chunk in self._stream_audit_logs_csv(filters):
            yield chunk

    async def export_login_logs(
        self,
        ctx: UseCaseContext,
        filters: LoginLogFilters,
    ) -> AsyncIterator[bytes]:
        """流式导出登录日志为 CSV — SPEC 18.3."""

        await self._record_export_audit_event(
            ctx,
            action="audit.login_log.export",
            resource_type="login_log",
            filter_summary=_describe_login_filters(filters),
        )

        async for chunk in self._stream_login_logs_csv(filters):
            yield chunk

    async def _record_export_audit_event(
        self,
        ctx: UseCaseContext,
        *,
        action: str,
        resource_type: str,
        filter_summary: str,
    ) -> None:
        """记录导出操作的审计事件 — SPEC 18.3.

        SPEC 18.3: "导出属于受控操作并记录新的审计事件"。
        导出审计事件在独立事务中提交，确保在流式输出开始前持久化。
        """

        from app.modules.audit.models import AuditEntry

        entry = AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="audit",
            action=action,
            resource_type=resource_type,
            resource_id=None,
            resource_display_name=filter_summary,
            result="success",
            request_id=ctx.request_id or None,
            diff=None,
            occurred_at=self._clock.now(),
        )

        async with self._uow_factory() as uow:
            audit = self._create_audit(uow.session)
            await audit.record_audit(entry)
            await uow.commit()

    async def _stream_audit_logs_csv(
        self,
        filters: AuditLogFilters,
    ) -> AsyncIterator[bytes]:
        """分批查询审计日志并流式输出 CSV."""

        # 输出 CSV 列头
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(AUDIT_LOG_CSV_COLUMNS)
        yield buf.getvalue().encode("utf-8")

        offset = 0
        while True:
            async with self._uow_factory() as uow:
                query = self._create_audit_query(uow.session)
                items, _ = await query.query_audit_logs(
                    filters,
                    offset,
                    _EXPORT_BATCH_SIZE,
                )

            if not items:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for entry in items:
                writer.writerow(_audit_entry_to_csv_row(entry))
            yield buf.getvalue().encode("utf-8")

            offset += _EXPORT_BATCH_SIZE

    async def _stream_login_logs_csv(
        self,
        filters: LoginLogFilters,
    ) -> AsyncIterator[bytes]:
        """分批查询登录日志并流式输出 CSV."""

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(LOGIN_LOG_CSV_COLUMNS)
        yield buf.getvalue().encode("utf-8")

        offset = 0
        while True:
            async with self._uow_factory() as uow:
                query = self._create_login_query(uow.session)
                items, _ = await query.query_login_logs(
                    filters,
                    offset,
                    _EXPORT_BATCH_SIZE,
                )

            if not items:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for entry in items:
                writer.writerow(_login_entry_to_csv_row(entry))
            yield buf.getvalue().encode("utf-8")

            offset += _EXPORT_BATCH_SIZE


# ── 响应转换辅助 ──────────────────────────────────────────────────────────


def _audit_entry_to_response(entry: AuditEntry) -> dict[str, object]:
    """审计日志领域实体 → 响应字典."""

    return {
        "id": entry.id,
        "actor_id": entry.actor_id,
        "actor_display_name": entry.actor_display_name,
        "module": entry.module,
        "action": entry.action,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "resource_display_name": entry.resource_display_name,
        "result": entry.result,
        "request_id": entry.request_id,
        "diff": entry.diff.to_dict() if entry.diff is not None else None,
        "occurred_at": entry.occurred_at,
    }


def _login_entry_to_response(entry: LoginLogEntry) -> dict[str, object]:
    """登录日志领域实体 → 响应字典."""

    return {
        "id": entry.id,
        "user_id": entry.user_id,
        "username": entry.username,
        "session_id": entry.session_id,
        "ip_address": entry.ip_address,
        "user_agent": entry.user_agent,
        "result": entry.result,
        "failure_reason": entry.failure_reason,
        "occurred_at": entry.occurred_at,
    }


def _audit_entry_to_csv_row(entry: AuditEntry) -> list[str]:
    """审计日志领域实体 → CSV 行."""

    return [
        str(entry.id),
        entry.actor_id or "",
        entry.actor_display_name,
        entry.module,
        entry.action,
        entry.resource_type,
        entry.resource_id or "",
        entry.resource_display_name or "",
        entry.result,
        entry.request_id or "",
        entry.occurred_at.isoformat(),
    ]


def _login_entry_to_csv_row(entry: LoginLogEntry) -> list[str]:
    """登录日志领域实体 → CSV 行."""

    return [
        str(entry.id),
        entry.user_id or "",
        entry.username,
        entry.session_id or "",
        entry.ip_address,
        entry.user_agent or "",
        entry.result,
        entry.failure_reason or "",
        entry.occurred_at.isoformat(),
    ]


def _describe_audit_filters(filters: AuditLogFilters) -> str:
    """生成审计日志筛选条件摘要（用于审计事件记录）."""

    parts: list[str] = []
    if filters.actor_id is not None:
        parts.append(f"actor={filters.actor_id}")
    if filters.module is not None:
        parts.append(f"module={filters.module}")
    if filters.action is not None:
        parts.append(f"action={filters.action}")
    if filters.resource_type is not None:
        parts.append(f"resource_type={filters.resource_type}")
    if filters.resource_id is not None:
        parts.append(f"resource_id={filters.resource_id}")
    if filters.result is not None:
        parts.append(f"result={filters.result}")
    if filters.start_time is not None:
        parts.append(f"from={filters.start_time.isoformat()}")
    if filters.end_time is not None:
        parts.append(f"to={filters.end_time.isoformat()}")
    return ", ".join(parts) if parts else "no_filter"


def _describe_login_filters(filters: LoginLogFilters) -> str:
    """生成登录日志筛选条件摘要."""

    parts: list[str] = []
    if filters.user_id is not None:
        parts.append(f"user={filters.user_id}")
    if filters.username is not None:
        parts.append(f"username={filters.username}")
    if filters.ip_address is not None:
        parts.append(f"ip={filters.ip_address}")
    if filters.result is not None:
        parts.append(f"result={filters.result}")
    if filters.start_time is not None:
        parts.append(f"from={filters.start_time.isoformat()}")
    if filters.end_time is not None:
        parts.append(f"to={filters.end_time.isoformat()}")
    return ", ".join(parts) if parts else "no_filter"
