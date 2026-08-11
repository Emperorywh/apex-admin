"""审计查询与保留治理单元测试 — SPEC 18.3 / 18.4 / 25.3.

覆盖验收标准:
  - AC-3: 保留期限配置、清理命令 dry-run/--apply、安全事件独立保留策略。
  - AC-4: 查询筛选条件、分页响应结构。

不依赖数据库（unit marker）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.audit.query_port import AuditLogFilters, LoginLogFilters
from app.modules.audit.retention import (
    CleanupResult,
    RetentionConfig,
    cutoff_for,
    format_cleanup_report,
)

# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 查询筛选条件
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestAuditLogFilters:
    """审计日志筛选条件测试 — SPEC 18.3."""

    def test_default_all_none(self) -> None:
        """默认所有筛选条件为 None（不筛选）。"""

        filters = AuditLogFilters()
        assert filters.actor_id is None
        assert filters.module is None
        assert filters.action is None
        assert filters.resource_type is None
        assert filters.resource_id is None
        assert filters.result is None
        assert filters.start_time is None
        assert filters.end_time is None

    def test_all_fields_set(self) -> None:
        """所有筛选字段可设置。"""

        now = datetime(2026, 8, 11, tzinfo=UTC)
        filters = AuditLogFilters(
            actor_id="user-001",
            module="identity",
            action="user.create",
            resource_type="user",
            resource_id="uuid-001",
            result="success",
            start_time=now - timedelta(days=7),
            end_time=now,
        )
        assert filters.actor_id == "user-001"
        assert filters.module == "identity"
        assert filters.action == "user.create"
        assert filters.resource_type == "user"
        assert filters.resource_id == "uuid-001"
        assert filters.result == "success"
        assert filters.start_time is not None
        assert filters.end_time is not None

    def test_immutable(self) -> None:
        """筛选条件为不可变 dataclass。"""

        filters = AuditLogFilters(actor_id="u1")
        with pytest.raises(AttributeError):
            filters.actor_id = "u2"  # type: ignore[misc]


@pytest.mark.g3
@pytest.mark.unit
class TestLoginLogFilters:
    """登录日志筛选条件测试 — SPEC 18.1 / 18.3."""

    def test_default_all_none(self) -> None:
        """默认所有筛选条件为 None。"""

        filters = LoginLogFilters()
        assert filters.user_id is None
        assert filters.username is None
        assert filters.ip_address is None
        assert filters.result is None
        assert filters.start_time is None
        assert filters.end_time is None

    def test_immutable(self) -> None:
        """筛选条件为不可变 dataclass。"""

        filters = LoginLogFilters(user_id="u1")
        with pytest.raises(AttributeError):
            filters.user_id = "u2"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 保留期限与清理命令
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestRetentionConfig:
    """保留配置测试 — SPEC 18.4."""

    def test_construction(self) -> None:
        """保留配置正确构造。"""

        config = RetentionConfig(
            audit_log_retention_days=180,
            login_log_retention_days=90,
            security_event_retention_days=365,
        )
        assert config.audit_log_retention_days == 180
        assert config.login_log_retention_days == 90
        assert config.security_event_retention_days == 365

    def test_security_event_retention_independent(self) -> None:
        """安全事件保留期限独立于普通访问日志 — SPEC 18.4.

        SPEC 18.4: "安全事件的保留策略独立于普通访问日志"。
        """

        config = RetentionConfig(
            audit_log_retention_days=30,
            login_log_retention_days=30,
            security_event_retention_days=999,
        )
        assert config.security_event_retention_days != config.audit_log_retention_days
        assert config.security_event_retention_days != config.login_log_retention_days

    def test_immutable(self) -> None:
        """保留配置为不可变 dataclass。"""

        config = RetentionConfig(
            audit_log_retention_days=180,
            login_log_retention_days=90,
            security_event_retention_days=365,
        )
        with pytest.raises(AttributeError):
            config.audit_log_retention_days = 1  # type: ignore[misc]


@pytest.mark.g3
@pytest.mark.unit
class TestCutoffCalculation:
    """截止时间计算测试 — SPEC 18.4."""

    def test_cutoff_for_90_days(self) -> None:
        """90 天保留期限的截止时间正确。"""

        now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        cutoff = cutoff_for(90, now)
        expected = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
        assert cutoff == expected

    def test_cutoff_for_0_days(self) -> None:
        """0 天保留期限——截止时间等于当前时间。"""

        now = datetime(2026, 8, 11, tzinfo=UTC)
        cutoff = cutoff_for(0, now)
        assert cutoff == now


@pytest.mark.g3
@pytest.mark.unit
class TestCleanupResult:
    """清理结果测试 — SPEC 18.4: 清理操作记录执行结果。"""

    def test_dry_run_result(self) -> None:
        """dry-run 结果不记录删除数。"""

        now = datetime(2026, 8, 11, tzinfo=UTC)
        result = CleanupResult(
            applied=False,
            audit_log_cutoff=now - timedelta(days=180),
            login_log_cutoff=now - timedelta(days=90),
            audit_logs_expired=10,
            login_logs_expired=5,
            audit_logs_deleted=0,
            login_logs_deleted=0,
        )
        assert not result.applied
        assert result.audit_logs_expired == 10
        assert result.login_logs_expired == 5
        assert result.audit_logs_deleted == 0
        assert result.login_logs_deleted == 0

    def test_apply_result(self) -> None:
        """--apply 结果记录实际删除数。"""

        now = datetime(2026, 8, 11, tzinfo=UTC)
        result = CleanupResult(
            applied=True,
            audit_log_cutoff=now - timedelta(days=180),
            login_log_cutoff=now - timedelta(days=90),
            audit_logs_expired=10,
            login_logs_expired=5,
            audit_logs_deleted=10,
            login_logs_deleted=5,
        )
        assert result.applied
        assert result.audit_logs_deleted == 10
        assert result.login_logs_deleted == 5


@pytest.mark.g3
@pytest.mark.unit
class TestCleanupReportFormat:
    """清理结果报告格式测试 — SPEC 18.4."""

    def test_dry_run_report(self) -> None:
        """dry-run 报告包含关键信息。"""

        now = datetime(2026, 8, 11, tzinfo=UTC)
        result = CleanupResult(
            applied=False,
            audit_log_cutoff=now - timedelta(days=180),
            login_log_cutoff=now - timedelta(days=90),
            audit_logs_expired=42,
            login_logs_expired=17,
            audit_logs_deleted=0,
            login_logs_deleted=0,
        )
        report = format_cleanup_report(result)
        assert "dry-run" in report.lower()
        assert "42" in report
        assert "17" in report
        assert "未修改" in report

    def test_apply_report(self) -> None:
        """--apply 报告包含删除数量。"""

        now = datetime(2026, 8, 11, tzinfo=UTC)
        result = CleanupResult(
            applied=True,
            audit_log_cutoff=now - timedelta(days=180),
            login_log_cutoff=now - timedelta(days=90),
            audit_logs_expired=42,
            login_logs_expired=17,
            audit_logs_deleted=42,
            login_logs_deleted=17,
        )
        report = format_cleanup_report(result)
        assert "--apply" in report
        assert "42" in report
        assert "17" in report
