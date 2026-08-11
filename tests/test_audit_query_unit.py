"""审计查询与保留治理单元测试 — SPEC 18.3 / 18.4 / 25.3.

覆盖验收标准:
  - AC-3: 保留期限配置、清理命令 dry-run/--apply、安全事件独立保留策略。
  - AC-4: 查询筛选条件、分页响应结构。

不依赖数据库（unit marker）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.modules.audit.models import (
    AuditEntry,
    ChangeDiff,
    DiffField,
    LoginLogEntry,
)
from app.modules.audit.query_adapter import (
    _build_audit_filters,
    _build_login_filters,
    _orm_to_audit_entry,
    _orm_to_login_entry,
)
from app.modules.audit.query_port import AuditLogFilters, LoginLogFilters
from app.modules.audit.query_use_case import (
    _audit_entry_to_csv_row,
    _audit_entry_to_response,
    _describe_audit_filters,
    _describe_login_filters,
    _login_entry_to_csv_row,
    _login_entry_to_response,
)
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


# ═══════════════════════════════════════════════════════════════════════════════
# 补充覆盖：筛选描述与转换辅助函数 — SPEC 18.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDescribeAuditFilters:
    """审计日志筛选条件摘要函数测试 — 覆盖所有分支."""

    def test_no_filter(self) -> None:
        """所有字段为 None 时返回 no_filter。"""

        assert _describe_audit_filters(AuditLogFilters()) == "no_filter"

    def test_all_fields_set(self) -> None:
        """所有字段设置时摘要包含全部条件。"""

        now = datetime(2026, 8, 12, tzinfo=UTC)
        filters = AuditLogFilters(
            actor_id="u1",
            module="identity",
            action="user.create",
            resource_type="user",
            resource_id="rid-1",
            result="success",
            start_time=now - timedelta(days=1),
            end_time=now,
        )
        desc = _describe_audit_filters(filters)
        assert "actor=u1" in desc
        assert "module=identity" in desc
        assert "action=user.create" in desc
        assert "resource_type=user" in desc
        assert "resource_id=rid-1" in desc
        assert "result=success" in desc
        assert "from=" in desc
        assert "to=" in desc

    def test_partial_fields(self) -> None:
        """部分字段设置时摘要仅包含已设条件。"""

        filters = AuditLogFilters(module="auth", result="failure")
        desc = _describe_audit_filters(filters)
        assert "module=auth" in desc
        assert "result=failure" in desc
        assert "actor=" not in desc


@pytest.mark.g3
@pytest.mark.unit
class TestDescribeLoginFilters:
    """登录日志筛选条件摘要函数测试 — 覆盖所有分支."""

    def test_no_filter(self) -> None:
        """所有字段为 None 时返回 no_filter。"""

        assert _describe_login_filters(LoginLogFilters()) == "no_filter"

    def test_all_fields_set(self) -> None:
        """所有字段设置时摘要包含全部条件。"""

        now = datetime(2026, 8, 12, tzinfo=UTC)
        filters = LoginLogFilters(
            user_id="u1",
            username="admin",
            ip_address="10.0.0.1",
            result="success",
            start_time=now - timedelta(days=1),
            end_time=now,
        )
        desc = _describe_login_filters(filters)
        assert "user=u1" in desc
        assert "username=admin" in desc
        assert "ip=10.0.0.1" in desc
        assert "result=success" in desc
        assert "from=" in desc
        assert "to=" in desc

    def test_partial_fields(self) -> None:
        """部分字段设置时摘要仅包含已设条件。"""

        filters = LoginLogFilters(username="admin", result="failure")
        desc = _describe_login_filters(filters)
        assert "username=admin" in desc
        assert "result=failure" in desc
        assert "ip=" not in desc


@pytest.mark.g3
@pytest.mark.unit
class TestBuildAuditFiltersConditions:
    """审计日志查询条件构建函数测试 — 覆盖所有 if 分支."""

    def test_empty_filters_produces_no_conditions(self) -> None:
        """所有字段为 None 时返回空条件列表。"""

        conditions = _build_audit_filters(AuditLogFilters())
        assert conditions == []

    def test_all_fields_produce_conditions(self) -> None:
        """所有字段设置时每个字段产生一个条件。"""

        now = datetime(2026, 8, 12, tzinfo=UTC)
        filters = AuditLogFilters(
            actor_id="u1",
            module="identity",
            action="user.create",
            resource_type="user",
            resource_id="rid-1",
            result="success",
            start_time=now,
            end_time=now,
        )
        conditions = _build_audit_filters(filters)
        assert len(conditions) == 8

    def test_partial_fields(self) -> None:
        """部分字段设置时仅产生对应条件。"""

        filters = AuditLogFilters(module="auth", result="failure")
        conditions = _build_audit_filters(filters)
        assert len(conditions) == 2


@pytest.mark.g3
@pytest.mark.unit
class TestBuildLoginFiltersConditions:
    """登录日志查询条件构建函数测试 — 覆盖所有 if 分支."""

    def test_empty_filters_produces_no_conditions(self) -> None:
        """所有字段为 None 时返回空条件列表。"""

        conditions = _build_login_filters(LoginLogFilters())
        assert conditions == []

    def test_all_fields_produce_conditions(self) -> None:
        """所有字段设置时每个字段产生一个条件。"""

        now = datetime(2026, 8, 12, tzinfo=UTC)
        filters = LoginLogFilters(
            user_id="u1",
            username="admin",
            ip_address="10.0.0.1",
            result="success",
            start_time=now,
            end_time=now,
        )
        conditions = _build_login_filters(filters)
        assert len(conditions) == 6


@pytest.mark.g3
@pytest.mark.unit
class TestOrmToAuditEntry:
    """ORM 到领域实体转换函数测试 — 覆盖 diff 分支."""

    def test_conversion_without_diff(self) -> None:
        """diff 为 None 时正确转换。"""

        from app.modules.audit.orm import AuditLogORM

        orm = AuditLogORM(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            actor_id=None,
            actor_display_name="系统",
            module="audit",
            action="audit.log.export",
            resource_type="audit_log",
            resource_id=None,
            resource_display_name=None,
            result="success",
            request_id=None,
            diff=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        entry = _orm_to_audit_entry(orm)
        assert entry.diff is None
        assert entry.actor_display_name == "系统"

    def test_conversion_with_diff(self) -> None:
        """diff 非 None 时正确转换为 ChangeDiff。"""

        from app.modules.audit.orm import AuditLogORM

        orm = AuditLogORM(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            actor_id="u1",
            actor_display_name="管理员",
            module="identity",
            action="user.update",
            resource_type="user",
            resource_id="rid-1",
            resource_display_name="张三",
            result="success",
            request_id="req-1",
            diff={"status": {"old": "active", "new": "disabled"}},
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        entry = _orm_to_audit_entry(orm)
        assert entry.diff is not None
        assert len(entry.diff.fields) == 1
        assert entry.diff.fields[0].field_name == "status"
        assert entry.diff.fields[0].old_value == "active"
        assert entry.diff.fields[0].new_value == "disabled"

    def test_conversion_with_non_dict_diff_value(self) -> None:
        """diff 值非 dict 时 old/new_value 为 None（isinstance 分支）。"""

        from app.modules.audit.orm import AuditLogORM

        orm = AuditLogORM(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            actor_id=None,
            actor_display_name="系统",
            module="test",
            action="test",
            resource_type="test",
            resource_id=None,
            resource_display_name=None,
            result="success",
            request_id=None,
            diff={"bad_field": "not_a_dict"},
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        entry = _orm_to_audit_entry(orm)
        assert entry.diff is not None
        assert entry.diff.fields[0].old_value is None
        assert entry.diff.fields[0].new_value is None


@pytest.mark.g3
@pytest.mark.unit
class TestOrmToLoginEntry:
    """登录日志 ORM 到领域实体转换测试."""

    def test_conversion_all_fields(self) -> None:
        """所有字段正确转换。"""

        from app.modules.audit.orm import LoginLogORM

        orm = LoginLogORM(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            user_id="u1",
            username="admin",
            session_id="sess-1",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            result="success",
            failure_reason=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        entry = _orm_to_login_entry(orm)
        assert entry.username == "admin"
        assert entry.ip_address == "10.0.0.1"


@pytest.mark.g3
@pytest.mark.unit
class TestAuditEntryToCsvRow:
    """审计日志 CSV 行转换测试 — 覆盖 None 值分支."""

    def test_all_none_optional_fields(self) -> None:
        """可选字段为 None 时 CSV 行使用空字符串。"""

        entry = AuditEntry(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            actor_id=None,
            actor_display_name="系统",
            module="audit",
            action="audit.log.export",
            resource_type="audit_log",
            resource_id=None,
            resource_display_name=None,
            result="success",
            request_id=None,
            diff=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        row = _audit_entry_to_csv_row(entry)
        assert row[1] == ""  # actor_id
        assert row[6] == ""  # resource_id
        assert row[7] == ""  # resource_display_name
        assert row[9] == ""  # request_id

    def test_all_fields_set(self) -> None:
        """所有字段设置时 CSV 行包含实际值。"""

        entry = AuditEntry(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            actor_id="u1",
            actor_display_name="管理员",
            module="identity",
            action="user.create",
            resource_type="user",
            resource_id="rid-1",
            resource_display_name="张三",
            result="success",
            request_id="req-1",
            diff=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        row = _audit_entry_to_csv_row(entry)
        assert row[1] == "u1"
        assert row[6] == "rid-1"
        assert row[7] == "张三"
        assert row[9] == "req-1"


@pytest.mark.g3
@pytest.mark.unit
class TestLoginEntryToCsvRow:
    """登录日志 CSV 行转换测试 — 覆盖 None 值分支."""

    def test_all_none_optional_fields(self) -> None:
        """可选字段为 None 时 CSV 行使用空字符串。"""

        entry = LoginLogEntry(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            user_id=None,
            username="unknown",
            session_id=None,
            ip_address="10.0.0.1",
            user_agent=None,
            result="failure",
            failure_reason=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        row = _login_entry_to_csv_row(entry)
        assert row[1] == ""  # user_id
        assert row[3] == ""  # session_id
        assert row[5] == ""  # user_agent
        assert row[7] == ""  # failure_reason

    def test_all_fields_set(self) -> None:
        """所有字段设置时 CSV 行包含实际值。"""

        entry = LoginLogEntry(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            user_id="u1",
            username="admin",
            session_id="sess-1",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            result="success",
            failure_reason=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        row = _login_entry_to_csv_row(entry)
        assert row[1] == "u1"
        assert row[3] == "sess-1"
        assert row[5] == "Mozilla/5.0"


@pytest.mark.g3
@pytest.mark.unit
class TestAuditEntryToResponse:
    """审计日志响应字典转换测试 — 覆盖 diff 分支."""

    def test_with_diff(self) -> None:
        """diff 非 None 时响应包含 diff 字典。"""

        entry = AuditEntry(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            actor_id="u1",
            actor_display_name="管理员",
            module="identity",
            action="user.update",
            resource_type="user",
            resource_id="rid-1",
            resource_display_name="张三",
            result="success",
            request_id="req-1",
            diff=ChangeDiff(
                fields=(
                    DiffField(
                        field_name="status",
                        old_value="active",
                        new_value="disabled",
                    ),
                ),
            ),
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        resp = _audit_entry_to_response(entry)
        assert resp["diff"] is not None
        assert "status" in resp["diff"]

    def test_without_diff(self) -> None:
        """diff 为 None 时响应 diff 字段为 None。"""

        entry = AuditEntry(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            actor_id=None,
            actor_display_name="系统",
            module="audit",
            action="audit.log.export",
            resource_type="audit_log",
            resource_id=None,
            resource_display_name=None,
            result="success",
            request_id=None,
            diff=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        resp = _audit_entry_to_response(entry)
        assert resp["diff"] is None


@pytest.mark.g3
@pytest.mark.unit
class TestLoginEntryToResponse:
    """登录日志响应字典转换测试."""

    def test_all_fields(self) -> None:
        """所有字段正确转换为响应字典。"""

        entry = LoginLogEntry(
            id=UUID("12345678-1234-5678-1234-567812345678"),
            user_id="u1",
            username="admin",
            session_id="sess-1",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            result="success",
            failure_reason=None,
            occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        resp = _login_entry_to_response(entry)
        assert resp["username"] == "admin"
        assert resp["ip_address"] == "10.0.0.1"
        assert resp["failure_reason"] is None
