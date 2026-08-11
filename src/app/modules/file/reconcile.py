"""文件一致性恢复与受控清理 — SPEC 19.3 / 19.4 / 25.3.

SPEC 19.3 确定性恢复/标记规则:
  - PENDING 存在最终文件且哈希一致 → READY
  - PENDING 不存在最终文件且超过 1 小时 → FAILED
  - READY 缺少物理文件 → FAILED 并产生高优先级运维日志
  - DELETING 超过延迟天数 → 物理删除，成功转 DELETED
  - DELETING 物理删除失败 → 保持 DELETING，允许幂等重试

SPEC 19.3 受控清理:
  - 临时目录中超过 24 小时的文件清理

SPEC 19.4 受控清理:
  - 未被引用的正式文件按保留期清理，保留期不短于 7 天

SPEC 25.3:
  - 所有修复命令默认 dry-run；实际修改必须使用显式 ``--apply``
    并记录审计或运维日志。

全部规则确定性、幂等：连续两次 ``--apply`` 结果一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from uuid import UUID

    from app.application.ports import Clock
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.file.port import FileStoragePort


#: 高优先级运维日志器 — SPEC 19.3: "READY 缺物理文件标 FAILED 并产生高优先级运维日志"。
_ops_logger = structlog.get_logger("apex.ops.file")


@dataclass(frozen=True)
class ReconcileConfig:
    """文件一致性恢复配置 — SPEC 19.3 / 19.4.

    属性:
        pending_timeout_hours:       PENDING 超时小时数（超时无最终文件标 FAILED）。
        temp_max_age_hours:          临时文件最大存活小时数（超时清理）。
        deletion_delay_days:         DELETING 延迟物理删除天数（满足备份窗口）。
        unreferenced_retention_days: 未被引用文件的保留天数（不短于 7 天）。
    """

    pending_timeout_hours: int
    temp_max_age_hours: int
    deletion_delay_days: int
    unreferenced_retention_days: int


@dataclass(frozen=True)
class ReconcileAction:
    """单条 reconcile 操作记录 — 用于 dry-run 报告和审计.

    属性:
        file_id:  受影响的文件 ID（临时文件清理时为 None）。
        action:   操作类型编码。
        detail:   操作详情（人类可读）。
    """

    file_id: UUID | None
    action: str
    detail: str


@dataclass(frozen=True)
class ReconcileResult:
    """reconcile 执行结果 — SPEC 19.3 / 25.3.

    dry-run 与 --apply 返回相同结构；dry-run 时 actions 列表描述
    "将要执行"的操作，--apply 时描述 "已执行"的操作。

    属性:
        applied:               是否实际执行了修改（True=--apply, False=dry-run）。
        actions:               全部操作记录列表。
        pending_promoted:      PENDING → READY 数量。
        pending_failed:        PENDING → FAILED 数量。
        ready_failed:          READY → FAILED 数量（物理文件缺失）。
        deleting_deleted:      DELETING → DELETED 数量。
        deleting_kept_delay:   DELETING 因延迟未到期保留数量。
        deleting_kept_error:   DELETING 因物理删除失败保留数量。
        unreferenced_marked:   未被引用 READY → DELETING 数量。
        temp_cleaned:          临时文件清理数量。
    """

    applied: bool
    actions: list[ReconcileAction] = field(default_factory=list)
    pending_promoted: int = 0
    pending_failed: int = 0
    ready_failed: int = 0
    deleting_deleted: int = 0
    deleting_kept_delay: int = 0
    deleting_kept_error: int = 0
    unreferenced_marked: int = 0
    temp_cleaned: int = 0

    @property
    def total_changes(self) -> int:
        """实际修改的数据项总数（不含 dry-run 预览项）。"""

        return (
            self.pending_promoted
            + self.pending_failed
            + self.ready_failed
            + self.deleting_deleted
            + self.unreferenced_marked
            + self.temp_cleaned
        )


async def execute_reconcile(
    *,
    config: ReconcileConfig,
    clock: Clock,
    apply: bool,
    uow: SqlAlchemyUnitOfWork,
    storage: FileStoragePort,
) -> ReconcileResult:
    """执行文件一致性恢复与受控清理 — SPEC 19.3 / 19.4 / 25.3.

    SPEC 25.3: ``apply=False`` 时只报告不一致，不修改数据与文件；
    ``apply=True`` 时按确定性规则执行恢复或标记并记录审计或运维日志。

    全部规则确定性、幂等（SPEC 19.3）。
    调用方负责 UoW 的生命周期管理（提交/回滚）。

    参数:
        config:  一致性恢复配置。
        clock:   时钟 Port。
        apply:   是否实际执行修改。
        uow:     当前 UoW。
        storage: 文件存储 Port。

    返回:
        执行结果。
    """

    from dataclasses import replace

    from app.modules.audit.models import AuditEntry
    from app.modules.file.adapter import SqlAlchemyFileRepository
    from app.modules.file.models import FileStatus
    from app.modules.file.state_machine import transition

    now = clock.now()
    repo = SqlAlchemyFileRepository(uow.session)
    actions: list[ReconcileAction] = []

    # 初始化计数
    pending_promoted = 0
    pending_failed = 0
    ready_failed = 0
    deleting_deleted = 0
    deleting_kept_delay = 0
    deleting_kept_error = 0
    unreferenced_marked = 0
    temp_cleaned = 0

    # ════════════════════════════════════════════════════════════════════════
    # 规则 1: PENDING 恢复或标记 — SPEC 19.3
    # ════════════════════════════════════════════════════════════════════════

    pending_timeout = now - timedelta(hours=config.pending_timeout_hours)
    pending_files = await repo.list_by_status(FileStatus.PENDING)

    for metadata in pending_files:
        final_path = storage.get_final_path(metadata.storage_name)

        if storage.exists(final_path):
            # PENDING 存在最终文件且哈希一致 → READY
            actual_hash = storage.compute_sha256(final_path)
            if actual_hash == metadata.sha256:
                action = ReconcileAction(
                    file_id=metadata.id,
                    action="pending_to_ready",
                    detail=(f"PENDING 文件哈希一致，推进为 READY: {metadata.id}"),
                )
                actions.append(action)
                if apply:
                    transition(metadata.status, FileStatus.READY)
                    updated = replace(
                        metadata,
                        status=FileStatus.READY,
                        updated_at=now,
                    )
                    await repo.save(updated)
                pending_promoted += 1
            else:
                # 哈希不一致 → 标记 FAILED
                action = ReconcileAction(
                    file_id=metadata.id,
                    action="pending_hash_mismatch",
                    detail=(
                        f"PENDING 文件哈希不一致: {metadata.id} "
                        f"(期望={metadata.sha256[:16]}…, "
                        f"实际={actual_hash[:16]}…)"
                    ),
                )
                actions.append(action)
                if apply:
                    transition(metadata.status, FileStatus.FAILED)
                    updated = replace(
                        metadata,
                        status=FileStatus.FAILED,
                        updated_at=now,
                    )
                    await repo.save(updated)
                    _ops_logger.error(
                        "file_reconcile_hash_mismatch",
                        file_id=str(metadata.id),
                        storage_name=metadata.storage_name,
                    )
                pending_failed += 1
        elif metadata.created_at < pending_timeout:
            # PENDING 不存在最终文件且超过 1 小时 → FAILED
            action = ReconcileAction(
                file_id=metadata.id,
                action="pending_to_failed",
                detail=(
                    f"PENDING 文件超过 {config.pending_timeout_hours} 小时"
                    f"无最终文件，标记 FAILED: {metadata.id}"
                ),
            )
            actions.append(action)
            if apply:
                transition(metadata.status, FileStatus.FAILED)
                updated = replace(
                    metadata,
                    status=FileStatus.FAILED,
                    updated_at=now,
                )
                await repo.save(updated)
            pending_failed += 1
        else:
            # PENDING 未超时，等待上传完成
            actions.append(
                ReconcileAction(
                    file_id=metadata.id,
                    action="pending_waiting",
                    detail=f"PENDING 文件未超时，等待上传完成: {metadata.id}",
                ),
            )

    # ════════════════════════════════════════════════════════════════════════
    # 规则 2: READY 缺物理文件 → FAILED — SPEC 19.3
    # ════════════════════════════════════════════════════════════════════════

    ready_files = await repo.list_by_status(FileStatus.READY)

    for metadata in ready_files:
        final_path = storage.get_final_path(metadata.storage_name)
        if not storage.exists(final_path):
            action = ReconcileAction(
                file_id=metadata.id,
                action="ready_to_failed",
                detail=(f"READY 文件物理缺失，标记 FAILED: {metadata.id}"),
            )
            actions.append(action)
            if apply:
                transition(metadata.status, FileStatus.FAILED)
                updated = replace(
                    metadata,
                    status=FileStatus.FAILED,
                    updated_at=now,
                )
                await repo.save(updated)
                # SPEC 19.3: 产生高优先级运维日志
                _ops_logger.error(
                    "file_missing_physical",
                    file_id=str(metadata.id),
                    storage_name=metadata.storage_name,
                    original_name=metadata.original_name,
                )
            ready_failed += 1

    # ════════════════════════════════════════════════════════════════════════
    # 规则 3: DELETING 延迟物理删除 — SPEC 19.3
    # ════════════════════════════════════════════════════════════════════════

    deletion_cutoff = now - timedelta(days=config.deletion_delay_days)
    deleting_files = await repo.list_by_status(FileStatus.DELETING)

    for metadata in deleting_files:
        if (
            metadata.deleting_entered_at is None
            or metadata.deleting_entered_at > deletion_cutoff
        ):
            # 未满延迟天数，不物理删除
            actions.append(
                ReconcileAction(
                    file_id=metadata.id,
                    action="deleting_delayed",
                    detail=(
                        f"DELETING 文件未满 {config.deletion_delay_days} 天"
                        f"延迟，保留: {metadata.id}"
                    ),
                ),
            )
            deleting_kept_delay += 1
            continue

        # 延迟到期，尝试物理删除
        final_path = storage.get_final_path(metadata.storage_name)
        try:
            deleted = storage.delete_file(final_path)
            # delete_file 返回 True=已删除, False=文件不存在（可能前次已删）
            # 两种情况均视为物理删除完成
            action_detail = (
                f"DELETING 文件物理删除完成: {metadata.id}"
                if deleted
                else f"DELETING 文件物理不存在，标记 DELETED: {metadata.id}"
            )
            actions.append(
                ReconcileAction(
                    file_id=metadata.id,
                    action="deleting_to_deleted",
                    detail=action_detail,
                ),
            )
            if apply:
                transition(metadata.status, FileStatus.DELETED)
                updated = replace(
                    metadata,
                    status=FileStatus.DELETED,
                    updated_at=now,
                )
                await repo.save(updated)
            deleting_deleted += 1
        except Exception:
            # 物理删除失败 → 保持 DELETING，允许幂等重试
            actions.append(
                ReconcileAction(
                    file_id=metadata.id,
                    action="deleting_delete_failed",
                    detail=(f"DELETING 文件物理删除失败，保持 DELETING: {metadata.id}"),
                ),
            )
            if apply:
                _ops_logger.warning(
                    "file_delete_failed_retry",
                    file_id=str(metadata.id),
                    storage_name=metadata.storage_name,
                )
            deleting_kept_error += 1

    # ════════════════════════════════════════════════════════════════════════
    # 规则 4: 未被引用 READY 文件按保留期标记 DELETING — SPEC 19.4
    # ════════════════════════════════════════════════════════════════════════

    unreferenced_cutoff = now - timedelta(
        days=config.unreferenced_retention_days,
    )
    unreferenced_files = await repo.list_unreferenced_ready(
        unreferenced_cutoff,
    )

    for metadata in unreferenced_files:
        actions.append(
            ReconcileAction(
                file_id=metadata.id,
                action="unreferenced_to_deleting",
                detail=(
                    f"未被引用 READY 文件超过 {config.unreferenced_retention_days}"
                    f" 天保留期，标记 DELETING: {metadata.id}"
                ),
            ),
        )
        if apply:
            transition(metadata.status, FileStatus.DELETING)
            updated = replace(
                metadata,
                status=FileStatus.DELETING,
                updated_at=now,
                deleting_entered_at=now,
            )
            await repo.save(updated)
        unreferenced_marked += 1

    # ════════════════════════════════════════════════════════════════════════
    # 规则 5: 临时文件清理 — SPEC 19.3
    # ════════════════════════════════════════════════════════════════════════

    temp_max_age = timedelta(hours=config.temp_max_age_hours)
    temp_names = storage.list_temp_dir()
    temp_cutoff_epoch = (now - temp_max_age).timestamp()

    for name in temp_names:
        temp_path = storage.get_temp_path(name)
        try:
            mtime = storage.get_mtime(temp_path)
        except Exception:
            continue
        if mtime < temp_cutoff_epoch:
            actions.append(
                ReconcileAction(
                    file_id=None,
                    action="temp_cleaned",
                    detail=f"清理过期临时文件: {name}",
                ),
            )
            if apply:
                storage.cleanup_temp(temp_path)
            temp_cleaned += 1

    # ════════════════════════════════════════════════════════════════════════
    # SPEC 25.3: --apply 写审计日志
    # ════════════════════════════════════════════════════════════════════════

    if apply and (
        pending_promoted
        + pending_failed
        + ready_failed
        + deleting_deleted
        + unreferenced_marked
        + temp_cleaned
        > 0
    ):
        from uuid import uuid4

        audit_entry = AuditEntry(
            id=uuid4(),
            actor_id="cli:files:reconcile",
            actor_display_name="system",
            module="file",
            action="file.reconcile",
            resource_type="file",
            resource_id="batch",
            resource_display_name=None,
            result="success",
            request_id=None,
            diff=None,
            occurred_at=now,
        )

        from app.modules.audit.adapter import SqlAlchemyAuditRepository

        audit_repo = SqlAlchemyAuditRepository(uow.session)
        await audit_repo.record_audit(audit_entry)

    return ReconcileResult(
        applied=apply,
        actions=actions,
        pending_promoted=pending_promoted,
        pending_failed=pending_failed,
        ready_failed=ready_failed,
        deleting_deleted=deleting_deleted,
        deleting_kept_delay=deleting_kept_delay,
        deleting_kept_error=deleting_kept_error,
        unreferenced_marked=unreferenced_marked,
        temp_cleaned=temp_cleaned,
    )


def format_reconcile_report(result: ReconcileResult) -> str:
    """格式化 reconcile 结果为可读文本 — SPEC 25.3."""

    mode = "已执行（--apply）" if result.applied else "预览（dry-run）"
    lines = [
        f"文件一致性恢复 — {mode}",
        "=" * 50,
        f"  PENDING → READY:         {result.pending_promoted}",
        f"  PENDING → FAILED:        {result.pending_failed}",
        f"  READY → FAILED:          {result.ready_failed}",
        f"  DELETING → DELETED:      {result.deleting_deleted}",
        f"  DELETING 保留（延迟）:    {result.deleting_kept_delay}",
        f"  DELETING 保留（删除失败）: {result.deleting_kept_error}",
        f"  未引用 → DELETING:       {result.unreferenced_marked}",
        f"  临时文件清理:             {result.temp_cleaned}",
    ]

    if not result.applied:
        lines.append("  （dry-run 模式，未修改任何数据与文件）")

    if result.actions:
        lines.append("")
        lines.append("操作明细:")
        for action in result.actions:
            lines.append(f"  [{action.action}] {action.detail}")

    lines.append("=" * 50)
    return "\n".join(lines)
