"""审计模块单元测试（SPEC §18.1–18.2、§5.7）。

覆盖：
- 操作审计模型字段完整性
- 登录日志模型字段完整性
- 审计 Port 显式调用（非装饰器）
- 事务内审计记录与业务数据同提交/回滚
- 敏感字段过滤（password、token、key）
- 审计差异字段白名单（禁止空白名单、禁止反射式序列化）
- 操作者/目标显示名称快照
- 失败操作独立安全日志（不在已回滚事务中）
- 权限/角色/用户状态变更审计支持
- 审计记录不可通过 CRUD 修改（无 update/delete 路由）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from app.modules.audit.domain.diff import (
    SENSITIVE_FIELDS,
    AuditDiff,
    FieldChange,
    compute_diff,
)
from app.modules.audit.domain.model import (
    AuditLog,
    AuditResult,
    LoginLog,
    LoginResult,
)
from app.modules.audit.infrastructure.security_log import SecurityEventLogger

pytestmark = [pytest.mark.unit, pytest.mark.g2]


# ---------------------------------------------------------------------------
# Fake UoW（内存实现，验证事务内审计记录）
# ---------------------------------------------------------------------------


class FakeAuditRepository:
    """内存操作审计 Repository，记录提交/回滚状态。"""

    def __init__(self) -> None:
        self._records: list[AuditLog] = []

    async def add(self, entity: AuditLog) -> None:
        self._records.append(entity)

    async def get_by_id(self, audit_id: UUID) -> AuditLog | None:
        return next((r for r in self._records if r.id == audit_id), None)


class FakeLoginLogRepository:
    """内存登录日志 Repository。"""

    def __init__(self) -> None:
        self._records: list[LoginLog] = []

    async def add(self, entity: LoginLog) -> None:
        self._records.append(entity)


class FakeAuditUnitOfWork:
    """内存审计 UoW，记录提交/回滚状态（SPEC §5.7 事务行为验证）。"""

    def __init__(self) -> None:
        self.audit_repo = FakeAuditRepository()
        self.login_repo = FakeLoginLogRepository()
        self.committed = False
        self.rolled_back = False
        self._active = False

    async def __aenter__(self) -> Self:
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._active = False
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True
            # 回滚时清除未提交的记录（模拟数据库回滚行为）
            self.audit_repo._records.clear()
            self.login_repo._records.clear()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
        self.audit_repo._records.clear()
        self.login_repo._records.clear()


# ---------------------------------------------------------------------------
# Fake AuditPort（内存实现，验证 Use Case 显式调用）
# ---------------------------------------------------------------------------


class FakeAuditPort:
    """内存审计 Port 实现，模拟 Use Case 显式调用审计记录。"""

    def __init__(self, uow: FakeAuditUnitOfWork) -> None:
        self._uow = uow

    async def record(  # noqa: PLR0913
        self,
        uow: object,
        *,
        actor_id: UUID | None,
        actor_display_name: str | None,
        occurred_at: datetime,
        module: str,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        resource_display_name: str | None = None,
        result: AuditResult,
        request_id: str | None = None,
        diff: AuditDiff | None = None,
    ) -> None:
        entity = AuditLog.new(
            actor_id=actor_id,
            actor_display_name=actor_display_name,
            occurred_at=occurred_at,
            module=module,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result=result,
            request_id=request_id,
            diff=diff,
        )
        await self._uow.audit_repo.add(entity)


# ---------------------------------------------------------------------------
# 操作审计模型测试（SPEC §18.2）
# ---------------------------------------------------------------------------


class TestAuditLogModel:
    """操作审计模型字段完整性测试（SPEC §18.2）。"""

    def test_audit_log_contains_all_required_fields(self) -> None:
        """操作审计模型包含全部必需字段：操作者身份、时间、模块、动作、
        目标资源类型/ID、结果、Request ID、变更差异。"""
        now = datetime.now(UTC)
        actor_id = uuid4()
        diff = AuditDiff(changes=(FieldChange(field="status", old="active", new="disabled"),))

        log = AuditLog.new(
            actor_id=actor_id,
            actor_display_name="管理员",
            occurred_at=now,
            module="user",
            action="user.status.change",
            resource_type="user:user",
            resource_id="some-uuid",
            resource_display_name="张三",
            result=AuditResult.SUCCESS,
            request_id="req-123",
            diff=diff,
        )

        assert log.actor_id == actor_id
        assert log.actor_display_name == "管理员"
        assert log.occurred_at == now
        assert log.module == "user"
        assert log.action == "user.status.change"
        assert log.resource_type == "user:user"
        assert log.resource_id == "some-uuid"
        assert log.resource_display_name == "张三"
        assert log.result == AuditResult.SUCCESS
        assert log.request_id == "req-123"
        assert log.diff == diff

    def test_audit_log_nullable_fields_for_unauthenticated(self) -> None:
        """未认证操作允许 actor_id 和 actor_display_name 为 None。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
            actor_id=None,
            actor_display_name=None,
            occurred_at=now,
            module="auth",
            action="auth.login",
            result=AuditResult.FAILED,
        )
        assert log.actor_id is None
        assert log.actor_display_name is None

    def test_audit_log_diff_none_for_no_change(self) -> None:
        """无变更差异时 diff 为 None。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="user",
            action="user.read",
            result=AuditResult.SUCCESS,
        )
        assert log.diff is None

    def test_audit_log_is_immutable(self) -> None:
        """审计实体不可变（frozen dataclass）。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="user",
            action="user.read",
            result=AuditResult.SUCCESS,
        )
        with pytest.raises(AttributeError):
            log.module = "auth"  # type: ignore[misc]

    def test_audit_log_auto_generates_id(self) -> None:
        """审计实体自动生成唯一 UUID。"""
        now = datetime.now(UTC)
        log1 = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="user",
            action="user.create",
            result=AuditResult.SUCCESS,
        )
        log2 = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="user",
            action="user.create",
            result=AuditResult.SUCCESS,
        )
        assert log1.id != log2.id


# ---------------------------------------------------------------------------
# 登录日志模型测试（SPEC §18.1）
# ---------------------------------------------------------------------------


class TestLoginLogModel:
    """登录日志模型字段完整性测试（SPEC §18.1）。"""

    def test_login_log_contains_all_required_fields(self) -> None:
        """登录日志模型包含用户、会话、IP、User-Agent、时间、结果、失败原因。"""
        now = datetime.now(UTC)
        user_id = uuid4()
        session_id = uuid4()

        log = LoginLog.new(
            user_id=user_id,
            username="zhangsan",
            session_id=session_id,
            ip="192.168.1.1",
            user_agent="Mozilla/5.0",
            occurred_at=now,
            result=LoginResult.LOGIN_SUCCESS,
        )

        assert log.user_id == user_id
        assert log.username == "zhangsan"
        assert log.session_id == session_id
        assert log.ip == "192.168.1.1"
        assert log.user_agent == "Mozilla/5.0"
        assert log.occurred_at == now
        assert log.result == LoginResult.LOGIN_SUCCESS
        assert log.failure_reason is None

    def test_login_log_failure_with_reason(self) -> None:
        """登录失败记录失败原因。"""
        now = datetime.now(UTC)
        log = LoginLog.new(
            user_id=None,
            username="unknown",
            session_id=None,
            ip="10.0.0.1",
            user_agent="curl/7.0",
            occurred_at=now,
            result=LoginResult.LOGIN_FAILED,
            failure_reason="invalid_credentials",
        )
        assert log.result == LoginResult.LOGIN_FAILED
        assert log.failure_reason == "invalid_credentials"

    def test_login_log_all_result_types(self) -> None:
        """登录日志支持全部结果类型（成功/失败/退出/Token异常/强制下线）。"""
        now = datetime.now(UTC)
        for result in LoginResult:
            log = LoginLog.new(
                user_id=uuid4(),
                username="user",
                session_id=None,
                ip="127.0.0.1",
                user_agent="test",
                occurred_at=now,
                result=result,
            )
            assert log.result == result

    def test_login_log_is_immutable(self) -> None:
        """登录日志实体不可变。"""
        now = datetime.now(UTC)
        log = LoginLog.new(
            user_id=uuid4(),
            username="user",
            session_id=None,
            ip="127.0.0.1",
            user_agent="test",
            occurred_at=now,
            result=LoginResult.LOGIN_SUCCESS,
        )
        with pytest.raises(AttributeError):
            log.result = LoginResult.LOGIN_FAILED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 审计差异与敏感字段过滤测试（SPEC §18.2）
# ---------------------------------------------------------------------------


class TestAuditDiff:
    """审计差异生成与敏感字段过滤测试（SPEC §18.2）。"""

    def test_diff_only_includes_whitelisted_fields(self) -> None:
        """差异只包含白名单中实际变化的字段。"""
        before = {"name": "old", "status": "active", "hidden": "secret"}
        after = {"name": "new", "status": "active", "hidden": "changed"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("name", "status"),
        )

        assert len(diff.changes) == 1
        assert diff.changes[0].field == "name"
        assert diff.changes[0].old == "old"
        assert diff.changes[0].new == "new"
        # hidden 不在白名单中，不进入差异
        assert all(c.field != "hidden" for c in diff.changes)

    def test_diff_excludes_password_field(self) -> None:
        """password 字段永不进入差异。"""
        before = {"username": "user", "password": "old_pass", "status": "active"}
        after = {"username": "user", "password": "new_pass", "status": "active"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("username", "password", "status"),
        )

        # password 即使在白名单中也被过滤
        assert all(c.field != "password" for c in diff.changes)
        assert diff.is_empty

    def test_diff_excludes_token_field(self) -> None:
        """token 字段永不进入差异。"""
        before = {"token": "abc", "name": "test"}
        after = {"token": "xyz", "name": "test"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("token", "name"),
        )

        assert all(c.field != "token" for c in diff.changes)
        assert diff.is_empty

    def test_diff_excludes_key_field(self) -> None:
        """key 字段永不进入差异。"""
        before = {"key": "secret_key", "name": "test"}
        after = {"key": "new_key", "name": "test"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("key", "name"),
        )

        assert all(c.field != "key" for c in diff.changes)
        assert diff.is_empty

    def test_diff_excludes_all_sensitive_fields(self) -> None:
        """全部敏感字段名均被过滤（不区分大小写）。"""
        before: dict[str, object] = {
            "password_hash": "old",
            "secret": "old",
            "authorization": "Bearer old",
            "verification_code": "1234",
            "captcha": "abcd",
            "refresh_token": "old_token",
            "access_token": "old_at",
            "name": "unchanged",
        }
        after: dict[str, object] = {
            "password_hash": "new",
            "secret": "new",
            "authorization": "Bearer new",
            "verification_code": "5678",
            "captcha": "efgh",
            "refresh_token": "new_token",
            "access_token": "new_at",
            "name": "unchanged",
        }

        all_fields = tuple(before.keys())
        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=all_fields,
        )

        # 所有敏感字段都被过滤，name 未变化
        assert diff.is_empty

    def test_diff_sensitive_field_case_insensitive(self) -> None:
        """敏感字段匹配不区分大小写。"""
        before = {"Password": "old", "TOKEN": "old"}
        after = {"Password": "new", "TOKEN": "new"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("Password", "TOKEN"),
        )

        assert diff.is_empty

    def test_diff_empty_whitelist_raises(self) -> None:
        """白名单禁止为空——禁止反射式全字段序列化。"""
        before = {"name": "old"}
        after = {"name": "new"}

        with pytest.raises(ValueError, match="白名单不得为空"):
            compute_diff(
                before=before,
                after=after,
                allowed_fields=(),
            )

    def test_diff_no_changes_returns_empty(self) -> None:
        """白名单中字段未变化时返回空差异。"""
        before = {"name": "same", "status": "active"}
        after = {"name": "same", "status": "active"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("name", "status"),
        )

        assert diff.is_empty
        assert diff.changes == ()

    def test_diff_multiple_changes(self) -> None:
        """多个字段变化时全部包含在差异中。"""
        before = {"name": "old", "status": "active", "email": "old@test.com"}
        after = {"name": "new", "status": "disabled", "email": "new@test.com"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("name", "status", "email"),
        )

        assert len(diff.changes) == 3
        change_fields = {c.field for c in diff.changes}
        assert change_fields == {"name", "status", "email"}

    def test_diff_none_values(self) -> None:
        """None 值正确处理。"""
        before = {"name": "test", "email": None}
        after = {"name": "test", "email": "new@test.com"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("name", "email"),
        )

        assert len(diff.changes) == 1
        assert diff.changes[0].field == "email"
        assert diff.changes[0].old is None
        assert diff.changes[0].new == "new@test.com"

    def test_sensitive_fields_constant_contains_core_sensitive_names(self) -> None:
        """SENSITIVE_FIELDS 常量包含核心敏感字段名。"""
        assert "password" in SENSITIVE_FIELDS
        assert "token" in SENSITIVE_FIELDS
        assert "key" in SENSITIVE_FIELDS
        assert "password_hash" in SENSITIVE_FIELDS

    def test_diff_immutable(self) -> None:
        """AuditDiff 和 FieldChange 不可变。"""
        change = FieldChange(field="name", old="old", new="new")
        with pytest.raises(AttributeError):
            change.field = "other"  # type: ignore[misc]

        diff = AuditDiff(changes=(change,))
        with pytest.raises(AttributeError):
            diff.changes = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 事务内审计测试（SPEC §5.7、§18.2）
# ---------------------------------------------------------------------------


class TestTransactionAudit:
    """事务内审计记录测试（SPEC §5.7、§18.2）。"""

    async def test_success_audit_committed_with_business_data(self) -> None:
        """成功操作的审计记录与业务数据在同一事务提交。"""
        uow = FakeAuditUnitOfWork()
        audit_port = FakeAuditPort(uow)
        now = datetime.now(UTC)

        async with uow:
            # 模拟 Use Case 显式调用审计 Port（SPEC §5.7）
            await audit_port.record(
                uow,
                actor_id=uuid4(),
                actor_display_name="管理员",
                occurred_at=now,
                module="user",
                action="user.create",
                resource_type="user:user",
                resource_id="resource-uuid",
                resource_display_name="张三",
                result=AuditResult.SUCCESS,
                request_id="req-001",
            )

        # 审计记录已提交（在 UoW 提交后保留）
        assert uow.committed is True
        assert uow.rolled_back is False
        assert len(uow.audit_repo._records) == 1

    async def test_failed_business_transaction_rolls_back_audit(self) -> None:
        """业务事务回滚时审计记录也被回滚（同事务）。"""
        uow = FakeAuditUnitOfWork()
        audit_port = FakeAuditPort(uow)
        now = datetime.now(UTC)

        with pytest.raises(ValueError):
            async with uow:
                await audit_port.record(
                    uow,
                    actor_id=uuid4(),
                    actor_display_name="管理员",
                    occurred_at=now,
                    module="user",
                    action="user.create",
                    result=AuditResult.SUCCESS,
                    request_id="req-002",
                )
                raise ValueError("业务异常")

        # 事务回滚——审计记录也回滚
        assert uow.rolled_back is True
        assert uow.committed is False
        assert len(uow.audit_repo._records) == 0

    async def test_audit_port_called_explicitly_by_use_case(self) -> None:
        """审计 Port 由 Use Case 显式调用（非装饰器/中间件）。"""
        uow = FakeAuditUnitOfWork()
        audit_port = FakeAuditPort(uow)
        now = datetime.now(UTC)

        # 模拟 Use Case 编排
        async with uow:
            await audit_port.record(
                uow,
                actor_id=uuid4(),
                actor_display_name="管理员",
                occurred_at=now,
                module="rbac",
                action="role.assign_permission",
                resource_type="rbac:role",
                resource_id=str(uuid4()),
                resource_display_name="admin",
                result=AuditResult.SUCCESS,
                request_id="req-003",
            )

        record = uow.audit_repo._records[0]
        assert record.action == "role.assign_permission"
        assert record.module == "rbac"

    async def test_login_log_committed_in_transaction(self) -> None:
        """登录日志在同一事务提交。"""
        uow = FakeAuditUnitOfWork()
        now = datetime.now(UTC)

        async with uow:
            login_log = LoginLog.new(
                user_id=uuid4(),
                username="zhangsan",
                session_id=uuid4(),
                ip="192.168.1.1",
                user_agent="Mozilla/5.0",
                occurred_at=now,
                result=LoginResult.LOGIN_SUCCESS,
            )
            await uow.login_repo.add(login_log)

        assert uow.committed is True
        assert len(uow.login_repo._records) == 1

    async def test_login_log_rolled_back_with_failed_transaction(self) -> None:
        """事务回滚时登录日志也回滚。"""
        uow = FakeAuditUnitOfWork()
        now = datetime.now(UTC)

        with pytest.raises(RuntimeError):
            async with uow:
                login_log = LoginLog.new(
                    user_id=uuid4(),
                    username="zhangsan",
                    session_id=None,
                    ip="10.0.0.1",
                    user_agent="curl",
                    occurred_at=now,
                    result=LoginResult.LOGIN_FAILED,
                    failure_reason="invalid_credentials",
                )
                await uow.login_repo.add(login_log)
                raise RuntimeError("业务异常")

        assert uow.rolled_back is True
        assert len(uow.login_repo._records) == 0


# ---------------------------------------------------------------------------
# 显示名称快照测试（SPEC §18.2）
# ---------------------------------------------------------------------------


class TestDisplayNameSnapshot:
    """操作者/目标显示名称快照测试（SPEC §18.2）。"""

    def test_actor_display_name_snapshotted_at_record_time(self) -> None:
        """操作者显示名称按操作发生时快照保存。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="张三（管理员）",
            occurred_at=now,
            module="user",
            action="user.create",
            resource_type="user:user",
            resource_id="target-uuid",
            resource_display_name="李四",
            result=AuditResult.SUCCESS,
        )
        # 快照值原样保存
        assert log.actor_display_name == "张三（管理员）"

    def test_resource_display_name_snapshotted_at_record_time(self) -> None:
        """目标显示名称按操作发生时快照保存。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="rbac",
            action="role.update",
            resource_type="rbac:role",
            resource_id=str(uuid4()),
            resource_display_name="系统管理员角色",
            result=AuditResult.SUCCESS,
        )
        assert log.resource_display_name == "系统管理员角色"

    def test_display_name_snapshots_are_immutable_strings(self) -> None:
        """快照为不可变字符串值，与领域实体的当前状态无关。"""
        now = datetime.now(UTC)
        original_name = "张三"
        log = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name=original_name,
            occurred_at=now,
            module="user",
            action="user.read",
            result=AuditResult.SUCCESS,
            resource_display_name="目标用户",
        )
        # 即使外部变量变化，审计记录中的快照不变
        original_name = "李四"
        assert log.actor_display_name == "张三"


# ---------------------------------------------------------------------------
# 失败操作独立安全日志测试（SPEC §5.7）
# ---------------------------------------------------------------------------


class TestSecurityEventLogger:
    """失败操作独立安全日志测试（SPEC §5.7）。"""

    def test_log_operation_failed(self, caplog: pytest.LogCaptureFixture) -> None:
        """失败操作记录到独立安全日志。"""
        logger = SecurityEventLogger()
        with caplog.at_level(logging.WARNING, logger="app.audit.security"):
            logger.log_operation_failed(
                module="user",
                action="user.create",
                actor_id="actor-uuid",
                resource_type="user:user",
                resource_id="target-uuid",
                reason="validation_error",
                request_id="req-001",
            )

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert getattr(record, "event", None) == "operation_failed"
        assert getattr(record, "op_module", None) == "user"
        assert getattr(record, "reason", None) == "validation_error"
        assert record.levelno == logging.WARNING

    def test_log_login_event_success(self, caplog: pytest.LogCaptureFixture) -> None:
        """登录成功记录安全日志。"""
        logger = SecurityEventLogger()
        with caplog.at_level(logging.INFO, logger="app.audit.security"):
            logger.log_login_event(
                result="login_success",
                username="zhangsan",
                user_id="user-uuid",
                ip="192.168.1.1",
                user_agent="Mozilla/5.0",
            )

        record = caplog.records[0]
        assert getattr(record, "event", None) == "login_success"
        assert getattr(record, "username", None) == "zhangsan"
        assert record.levelno == logging.INFO

    def test_log_login_event_failed(self, caplog: pytest.LogCaptureFixture) -> None:
        """登录失败记录安全日志。"""
        logger = SecurityEventLogger()
        with caplog.at_level(logging.WARNING, logger="app.audit.security"):
            logger.log_login_event(
                result="login_failed",
                username="unknown",
                ip="10.0.0.1",
                user_agent="curl",
                reason="invalid_credentials",
            )

        record = caplog.records[0]
        assert getattr(record, "event", None) == "login_failed"
        assert getattr(record, "reason", None) == "invalid_credentials"

    def test_security_log_not_in_database_transaction(self) -> None:
        """安全日志使用结构化 Python 日志，不在数据库事务中。"""
        # SecurityEventLogger 不接收 UoW 参数——它与数据库事务完全解耦
        import inspect

        sig = inspect.signature(SecurityEventLogger.log_operation_failed)
        assert "uow" not in sig.parameters
        assert "session" not in sig.parameters

    def test_security_log_filters_sensitive_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        """安全日志过滤敏感字段——password、token 不出现在任何日志中。"""
        logger = SecurityEventLogger()
        with caplog.at_level(logging.WARNING, logger="app.audit.security"):
            logger.log_security_event(
                event="test_event",
                password="secret_password",
                token="secret_token",
                key="secret_key",
                op_module="test",
            )

        record = caplog.records[0]
        assert not hasattr(record, "password")
        assert not hasattr(record, "token")
        assert not hasattr(record, "key")
        assert getattr(record, "op_module", None) == "test"

    def test_security_log_only_allows_whitelisted_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """安全日志只记录白名单字段。"""
        logger = SecurityEventLogger()
        with caplog.at_level(logging.WARNING, logger="app.audit.security"):
            logger.log_security_event(
                event="test_event",
                op_module="test",
                random_field="should_be_ignored",
                another_field="also_ignored",
            )

        extra_keys = set(caplog.records[0].__dict__.keys())
        assert "op_module" in extra_keys
        assert "random_field" not in extra_keys
        assert "another_field" not in extra_keys


# ---------------------------------------------------------------------------
# 权限/角色/用户状态变更审计支持测试（SPEC §18.2）
# ---------------------------------------------------------------------------


class TestRequiredAuditScenarios:
    """权限/角色/用户状态变更必须审计的场景测试（SPEC §18.2）。"""

    async def test_user_status_change_can_be_audited(self) -> None:
        """用户状态变更必须审计——审计 Port 支持记录此类操作。"""
        uow = FakeAuditUnitOfWork()
        audit_port = FakeAuditPort(uow)
        now = datetime.now(UTC)

        before = {"status": "active"}
        after = {"status": "disabled"}
        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("status",),
        )

        async with uow:
            await audit_port.record(
                uow,
                actor_id=uuid4(),
                actor_display_name="超级管理员",
                occurred_at=now,
                module="user",
                action="user.status.change",
                resource_type="user:user",
                resource_id="target-uuid",
                resource_display_name="张三",
                result=AuditResult.SUCCESS,
                request_id="req-001",
                diff=diff,
            )

        record = uow.audit_repo._records[0]
        assert record.action == "user.status.change"
        assert record.module == "user"
        assert record.diff is not None
        assert len(record.diff.changes) == 1
        assert record.diff.changes[0].field == "status"
        assert record.diff.changes[0].old == "active"
        assert record.diff.changes[0].new == "disabled"

    async def test_permission_change_can_be_audited(self) -> None:
        """权限变更必须审计——审计 Port 支持记录权限分配操作。"""
        uow = FakeAuditUnitOfWork()
        audit_port = FakeAuditPort(uow)
        now = datetime.now(UTC)

        async with uow:
            await audit_port.record(
                uow,
                actor_id=uuid4(),
                actor_display_name="管理员",
                occurred_at=now,
                module="rbac",
                action="role.assign_permission",
                resource_type="rbac:role",
                resource_id=str(uuid4()),
                resource_display_name="系统管理员",
                result=AuditResult.SUCCESS,
                request_id="req-002",
            )

        record = uow.audit_repo._records[0]
        assert record.module == "rbac"
        assert record.action == "role.assign_permission"

    async def test_role_change_can_be_audited(self) -> None:
        """角色变更必须审计——审计 Port 支持记录角色分配操作。"""
        uow = FakeAuditUnitOfWork()
        audit_port = FakeAuditPort(uow)
        now = datetime.now(UTC)

        before = {"status": "active"}
        after = {"status": "disabled"}
        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("status",),
        )

        async with uow:
            await audit_port.record(
                uow,
                actor_id=uuid4(),
                actor_display_name="管理员",
                occurred_at=now,
                module="rbac",
                action="role.update",
                resource_type="rbac:role",
                resource_id=str(uuid4()),
                resource_display_name="编辑角色",
                result=AuditResult.SUCCESS,
                request_id="req-003",
                diff=diff,
            )

        record = uow.audit_repo._records[0]
        assert record.action == "role.update"
        assert record.module == "rbac"


# ---------------------------------------------------------------------------
# 审计记录不可通过 CRUD 修改测试（SPEC §18.2）
# ---------------------------------------------------------------------------


class TestAuditImmutable:
    """审计记录不可通过普通 CRUD 修改测试（SPEC §18.2）。"""

    def test_audit_repository_has_no_update_method(self) -> None:
        """AuditRepository 端口不暴露 update 方法。"""
        from app.modules.audit.application.port import AuditRepository

        assert not hasattr(AuditRepository, "update")

    def test_audit_repository_has_no_delete_method(self) -> None:
        """AuditRepository 端口不暴露 delete 方法。"""
        from app.modules.audit.application.port import AuditRepository

        assert not hasattr(AuditRepository, "delete")

    def test_login_log_repository_has_no_update_method(self) -> None:
        """LoginLogRepository 端口不暴露 update 方法。"""
        from app.modules.audit.application.port import LoginLogRepository

        assert not hasattr(LoginLogRepository, "update")

    def test_login_log_repository_has_no_delete_method(self) -> None:
        """LoginLogRepository 端口不暴露 delete 方法。"""
        from app.modules.audit.application.port import LoginLogRepository

        assert not hasattr(LoginLogRepository, "delete")

    def test_audit_module_has_no_routes(self) -> None:
        """审计模块不暴露任何 CRUD 路由（G2 阶段）。"""
        from app.modules.audit.definition import MODULE

        assert len(MODULE.routers) == 0


# ---------------------------------------------------------------------------
# 模块定义测试
# ---------------------------------------------------------------------------


class TestAuditModuleDefinition:
    """审计模块定义测试。"""

    def test_module_code_is_audit(self) -> None:
        """模块编码为 ``audit``。"""
        from app.modules.audit.definition import MODULE

        assert MODULE.code == "audit"

    def test_module_registered_in_composition_root(self) -> None:
        """审计模块已在 Composition Root 注册。"""
        from app.composition_root import get_enabled_modules

        codes = {m.code for m in get_enabled_modules()}
        assert "audit" in codes

    def test_module_has_migration(self) -> None:
        """审计模块迁移文件存在。"""
        from pathlib import Path

        migration = (
            Path(__file__).resolve().parents[2]
            / "src/app/infrastructure/database/migrations/versions/0007_audit.py"
        )
        assert migration.exists()
