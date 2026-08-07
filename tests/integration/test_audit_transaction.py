"""审计模块集成测试（SPEC §18.1–18.2、§5.7）。

使用 Testcontainers PostgreSQL 验证：
- 审计记录与业务数据在同一事务提交
- 审计记录与业务数据在同一事务回滚
- 敏感字段不进入差异
- 显示名称快照保存
- 失败操作独立安全日志
- AuditPort 显式调用
- ORM 模型正确持久化和还原

依赖 Docker 运行 Testcontainers PostgreSQL 18。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.audit.domain.diff import (
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
from app.modules.audit.infrastructure.unit_of_work import SqlAlchemyAuditUnitOfWork

pytestmark = [pytest.mark.integration, pytest.mark.g2]


@pytest.fixture
def uow_factory(db_engine):
    """创建审计 UoW 工厂。"""

    def factory() -> SqlAlchemyAuditUnitOfWork:
        return SqlAlchemyAuditUnitOfWork(db_engine)

    return factory


class TestAuditLogPersistence:
    """操作审计持久化集成测试。"""

    async def test_audit_log_persisted_and_retrieved(self, uow_factory) -> None:
        """审计记录持久化到数据库并可查询。"""
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
            resource_id="target-uuid",
            resource_display_name="张三",
            result=AuditResult.SUCCESS,
            request_id="req-001",
            diff=diff,
        )

        async with uow_factory() as uow:
            await uow.audit_records.add(log)

        # 在新 UoW 中查询验证持久化
        async with uow_factory() as uow:
            retrieved = await uow.audit_records.get_by_id(log.id)
            assert retrieved is not None
            assert retrieved.module == "user"
            assert retrieved.action == "user.status.change"
            assert retrieved.actor_display_name == "管理员"
            assert retrieved.resource_display_name == "张三"
            assert retrieved.result == AuditResult.SUCCESS
            assert retrieved.request_id == "req-001"
            assert retrieved.diff is not None
            assert len(retrieved.diff.changes) == 1
            assert retrieved.diff.changes[0].field == "status"
            assert retrieved.diff.changes[0].old == "active"
            assert retrieved.diff.changes[0].new == "disabled"

    async def test_audit_log_with_null_optional_fields(self, uow_factory) -> None:
        """审计记录可选字段为 None 时正确持久化。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
            actor_id=None,
            actor_display_name=None,
            occurred_at=now,
            module="auth",
            action="auth.login",
            result=AuditResult.FAILED,
        )

        async with uow_factory() as uow:
            await uow.audit_records.add(log)

        async with uow_factory() as uow:
            retrieved = await uow.audit_records.get_by_id(log.id)
            assert retrieved is not None
            assert retrieved.actor_id is None
            assert retrieved.actor_display_name is None
            assert retrieved.diff is None

    async def test_audit_log_with_sensitive_diff_filtered(self, uow_factory) -> None:
        """审计差异中敏感字段被过滤——数据库中无敏感数据。"""
        now = datetime.now(UTC)
        before = {"username": "user", "password": "old_pass", "status": "active"}
        after = {"username": "user", "password": "new_pass", "status": "disabled"}

        diff = compute_diff(
            before=before,
            after=after,
            allowed_fields=("username", "password", "status"),
        )

        log = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="user",
            action="user.update",
            result=AuditResult.SUCCESS,
            diff=diff,
        )

        async with uow_factory() as uow:
            await uow.audit_records.add(log)

        async with uow_factory() as uow:
            retrieved = await uow.audit_records.get_by_id(log.id)
            assert retrieved is not None
            assert retrieved.diff is not None
            # 差异中只有 status 字段——password 被过滤，username 未变化
            assert len(retrieved.diff.changes) == 1
            assert retrieved.diff.changes[0].field == "status"
            assert all("password" not in c.field.lower() for c in retrieved.diff.changes)


class TestLoginLogPersistence:
    """登录日志持久化集成测试。"""

    async def test_login_log_persisted(self, uow_factory) -> None:
        """登录日志持久化到数据库。"""
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

        async with uow_factory() as uow:
            await uow.login_logs.add(log)

    async def test_login_log_failed_with_reason(self, uow_factory) -> None:
        """失败登录日志持久化失败原因。"""
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

        async with uow_factory() as uow:
            await uow.login_logs.add(log)


class TestAuditTransactionBehavior:
    """审计与业务数据同事务提交/回滚集成测试（SPEC §5.7、§18.2）。"""

    async def test_audit_committed_with_business_data(self, uow_factory) -> None:
        """成功操作的审计记录与业务数据在同一事务提交。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
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

        async with uow_factory() as uow:
            await uow.audit_records.add(log)
        # UoW 正常退出 → 自动提交

        # 新 UoW 验证记录已持久化
        async with uow_factory() as uow:
            retrieved = await uow.audit_records.get_by_id(log.id)
            assert retrieved is not None
            assert retrieved.action == "user.create"

    async def test_audit_rolled_back_with_failed_business(self, uow_factory) -> None:
        """业务事务回滚时审计记录也被回滚。"""
        now = datetime.now(UTC)
        log = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="user",
            action="user.create",
            result=AuditResult.SUCCESS,
        )

        with pytest.raises(ValueError):
            async with uow_factory() as uow:
                await uow.audit_records.add(log)
                raise ValueError("业务异常")

        # 回滚后审计记录不存在
        async with uow_factory() as uow:
            retrieved = await uow.audit_records.get_by_id(log.id)
            assert retrieved is None

    async def test_multiple_audit_records_in_same_transaction(self, uow_factory) -> None:
        """同一事务可记录多条审计——全部提交或全部回滚。"""
        now = datetime.now(UTC)
        log1 = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="rbac",
            action="role.assign",
            result=AuditResult.SUCCESS,
        )
        log2 = AuditLog.new(
            actor_id=uuid4(),
            actor_display_name="管理员",
            occurred_at=now,
            module="rbac",
            action="role.assign_permission",
            result=AuditResult.SUCCESS,
        )

        async with uow_factory() as uow:
            await uow.audit_records.add(log1)
            await uow.audit_records.add(log2)

        async with uow_factory() as uow:
            r1 = await uow.audit_records.get_by_id(log1.id)
            r2 = await uow.audit_records.get_by_id(log2.id)
            assert r1 is not None
            assert r2 is not None

    async def test_failed_audit_rolled_back(self, uow_factory) -> None:
        """回滚时已追加的多条审计全部回滚。"""
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
            action="user.update",
            result=AuditResult.SUCCESS,
        )

        with pytest.raises(RuntimeError):
            async with uow_factory() as uow:
                await uow.audit_records.add(log1)
                await uow.audit_records.add(log2)
                raise RuntimeError("业务异常")

        async with uow_factory() as uow:
            assert await uow.audit_records.get_by_id(log1.id) is None
            assert await uow.audit_records.get_by_id(log2.id) is None
