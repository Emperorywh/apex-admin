"""审计日志保留治理 — SPEC 18.4 / 25.3.

SPEC 18.4:
  - 定义审计日志保留期限。
  - 提供受控的归档或清理命令。
  - 清理操作记录执行结果。
  - 安全事件的保留策略独立于普通访问日志。

SPEC 25.3: 所有修复命令默认 dry-run；实际修改必须使用显式 ``--apply``
并记录审计或运维日志。

保留期限通过部署配置（``Settings``）定义:
  - ``AUDIT_LOG_RETENTION_DAYS``: 审计日志保留天数。
  - ``LOGIN_LOG_RETENTION_DAYS``: 登录日志保留天数。
  - ``SECURITY_EVENT_RETENTION_DAYS``: 安全事件保留天数
    （独立于普通访问日志，安全事件通过 structlog 渠道记录，
    其轮转由 TASK-029/031 负责）。

清理流程:
  1. 根据保留期限计算截止时间。
  2. dry-run 模式只报告将清理的记录数。
  3. --apply 模式执行删除并记录执行结果。
  4. 执行结果作为安全事件记录到运维日志。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.application.ports import Clock
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork


@dataclass(frozen=True)
class RetentionConfig:
    """审计日志保留配置 — SPEC 18.4.

    属性:
        audit_log_retention_days:       审计日志保留天数。
        login_log_retention_days:       登录日志保留天数。
        security_event_retention_days:  安全事件保留天数
                                       （独立于普通访问日志，SPEC 18.4）。
    """

    audit_log_retention_days: int
    login_log_retention_days: int
    security_event_retention_days: int


@dataclass(frozen=True)
class CleanupResult:
    """清理命令执行结果 — SPEC 18.4: 清理操作记录执行结果.

    属性:
        applied:             是否实际执行了删除（True=--apply, False=dry-run）。
        audit_log_cutoff:    审计日志截止时间。
        login_log_cutoff:    登录日志截止时间。
        audit_logs_expired:  过期审计日志数量。
        login_logs_expired:  过期登录日志数量。
        audit_logs_deleted:  实际删除的审计日志数量（dry-run 时为 0）。
        login_logs_deleted:  实际删除的登录日志数量（dry-run 时为 0）。
    """

    applied: bool
    audit_log_cutoff: datetime
    login_log_cutoff: datetime
    audit_logs_expired: int
    login_logs_expired: int
    audit_logs_deleted: int
    login_logs_deleted: int


def cutoff_for(retention_days: int, now: datetime) -> datetime:
    """根据保留天数计算截止时间.

    参数:
        retention_days: 保留天数。
        now:            当前时间。

    返回:
        截止时间，``occurred_at`` 早于此时间的记录视为过期。
    """

    return now - timedelta(days=retention_days)


async def execute_cleanup(
    *,
    config: RetentionConfig,
    clock: Clock,
    apply: bool,
    uow: SqlAlchemyUnitOfWork,
) -> CleanupResult:
    """执行审计日志保留清理 — SPEC 18.4 / 25.3.

    SPEC 25.3: 默认 dry-run 不改数据；``apply=True`` 时执行删除。
    SPEC 18.4: 清理操作记录执行结果。

    调用方负责 UoW 的生命周期管理（提交/回滚）。

    参数:
        config: 保留配置。
        clock:  时钟 Port。
        apply:  是否实际执行删除（True=--apply, False=dry-run）。
        uow:    当前 UoW（提供事务会话）。

    返回:
        清理结果。
    """

    from app.modules.audit.query_adapter import SqlAlchemyAuditRetentionAdapter

    now = clock.now()
    audit_cutoff = cutoff_for(config.audit_log_retention_days, now)
    login_cutoff = cutoff_for(config.login_log_retention_days, now)

    retention = SqlAlchemyAuditRetentionAdapter(uow.session)

    audit_expired = await retention.count_expired_audit_logs(audit_cutoff)
    login_expired = await retention.count_expired_login_logs(login_cutoff)

    if not apply:
        return CleanupResult(
            applied=False,
            audit_log_cutoff=audit_cutoff,
            login_log_cutoff=login_cutoff,
            audit_logs_expired=audit_expired,
            login_logs_expired=login_expired,
            audit_logs_deleted=0,
            login_logs_deleted=0,
        )

    audit_deleted = await retention.delete_expired_audit_logs(audit_cutoff)
    login_deleted = await retention.delete_expired_login_logs(login_cutoff)

    return CleanupResult(
        applied=True,
        audit_log_cutoff=audit_cutoff,
        login_log_cutoff=login_cutoff,
        audit_logs_expired=audit_expired,
        login_logs_expired=login_expired,
        audit_logs_deleted=audit_deleted,
        login_logs_deleted=login_deleted,
    )


def format_cleanup_report(result: CleanupResult) -> str:
    """格式化清理结果为可读文本 — SPEC 18.4: 清理操作记录执行结果."""

    mode = "已执行（--apply）" if result.applied else "预览（dry-run）"
    lines = [
        f"审计日志保留清理 — {mode}",
        "=" * 50,
        f"  审计日志截止时间: {result.audit_log_cutoff.isoformat()}",
        f"  登录日志截止时间: {result.login_log_cutoff.isoformat()}",
        f"  过期审计日志: {result.audit_logs_expired} 条",
        f"  过期登录日志: {result.login_logs_expired} 条",
    ]

    if result.applied:
        lines.extend(
            [
                f"  已删除审计日志: {result.audit_logs_deleted} 条",
                f"  已删除登录日志: {result.login_logs_deleted} 条",
            ],
        )
    else:
        lines.append("  （dry-run 模式，未修改任何数据）")

    lines.append("=" * 50)
    return "\n".join(lines)
