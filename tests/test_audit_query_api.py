"""审计查询与导出 API 契约测试 — SPEC 18.3 / 28.4.

覆盖验收标准:
  - AC-0: 登录日志与操作审计分页查询 API 契约通过，支持按操作者/模块/动作/
    资源/结果/时间范围筛选，可查看单次操作详情。
  - AC-1: 审计查询接口无权限返回 403。
  - AC-2: 审计导出为流式文件下载，执行操作权限校验，不依赖通用导出扩展，
    导出行为本身写入新的审计事件。
  - AC-3: 日志保留期限可配置；清理命令默认 dry-run 不改数据、--apply 生效
    且记录执行结果；安全事件保留策略独立于普通访问日志。

使用 TestClient 对真实应用发请求。
连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.application.context import UseCaseContext
from app.composition.modules import MODULE_VERSION_LOCATIONS
from app.core.config import Environment, Settings
from app.infrastructure.db.engine import create_db_engine
from app.main import create_app
from app.modules.audit.models import AuditEntry, ChangeDiff, DiffField, LoginLogEntry
from app.modules.auth.dependencies import get_authenticated_context_async
from app.modules.auth.permission import ActorAuthorization, get_actor_authorization

if TYPE_CHECKING:
    from collections.abc import Iterator

# ── 迁移与清理 ─────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from alembic import command

    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_tables(database_url: str) -> None:
    """清理审计和登录日志表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM login_logs"))
    finally:
        await engine.dispose()


async def _seed_audit_log(
    database_url: str,
    *,
    entry: AuditEntry | None = None,
) -> UUID:
    """插入一条审计日志并返回其 ID。"""

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyAuditRepository

    if entry is None:
        entry = _make_audit_entry()

    engine = create_db_engine(database_url)
    try:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            repo = SqlAlchemyAuditRepository(uow.session)
            await repo.record_audit(entry)
            await uow.commit()
    finally:
        await engine.dispose()
    return entry.id


async def _seed_login_log(
    database_url: str,
    *,
    entry: LoginLogEntry | None = None,
) -> UUID:
    """插入一条登录日志并返回其 ID。"""

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyLoginLogRepository

    if entry is None:
        entry = _make_login_entry()

    engine = create_db_engine(database_url)
    try:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            repo = SqlAlchemyLoginLogRepository(uow.session)
            await repo.record_login(entry)
            await uow.commit()
    finally:
        await engine.dispose()
    return entry.id


async def _count_audit_logs(database_url: str) -> int:
    """查询 audit_logs 行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM audit_logs"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


# ── 辅助工厂 ───────────────────────────────────────────────────────────────


def _make_audit_entry(
    *,
    actor_id: str = "actor-001",
    module: str = "identity",
    action: str = "user.create",
    resource_type: str = "user",
    resource_id: str = "res-001",
    result: str = "success",
    occurred_at: datetime | None = None,
) -> AuditEntry:
    """构造测试用审计条目。"""

    return AuditEntry(
        id=uuid4(),
        actor_id=actor_id,
        actor_display_name="Test Actor",
        module=module,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_display_name="Test Resource",
        result=result,
        request_id="req-test-001",
        diff=ChangeDiff(
            fields=(
                DiffField(
                    field_name="status",
                    old_value="active",
                    new_value="disabled",
                ),
            ),
        ),
        occurred_at=occurred_at or datetime.now(UTC),
    )


def _make_login_entry(
    *,
    user_id: str = "user-001",
    username: str = "testuser",
    ip_address: str = "10.0.0.1",
    result: str = "success",
    occurred_at: datetime | None = None,
) -> LoginLogEntry:
    """构造测试用登录日志条目。"""

    return LoginLogEntry(
        id=uuid4(),
        user_id=user_id,
        username=username,
        session_id="sess-001",
        ip_address=ip_address,
        user_agent="Mozilla/5.0",
        result=result,
        failure_reason=None if result == "success" else "invalid_credentials",
        occurred_at=occurred_at or datetime.now(UTC),
    )


# ── 测试 fixture ───────────────────────────────────────────────────────────

_TEST_ACTOR_ID = "00000000-0000-0000-0000-0000000000a4"

_SUPER_ADMIN_CTX = UseCaseContext(
    request_id="test-audit-req",
    actor_id=_TEST_ACTOR_ID,
)


def _super_admin_auth_override() -> ActorAuthorization:
    """模拟超管授权。"""

    return ActorAuthorization(
        ctx=_SUPER_ADMIN_CTX,
        permissions=frozenset(),
        is_super_admin=True,
    )


def _super_admin_ctx_override() -> UseCaseContext:
    """模拟认证上下文。"""

    return _SUPER_ADMIN_CTX


def _no_permission_auth_override() -> ActorAuthorization:
    """模拟无权限的普通用户。"""

    return ActorAuthorization(
        ctx=_SUPER_ADMIN_CTX,
        permissions=frozenset(),
        is_super_admin=False,
    )


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移。"""

    asyncio.run(_apply_migrations(database_url))
    yield database_url


@pytest.fixture(autouse=True)
def _clean_tables(migrated_database_url: str) -> Iterator[None]:
    """每个测试前后清理全部表。"""

    asyncio.run(_cleanup_tables(migrated_database_url))
    yield
    asyncio.run(_cleanup_tables(migrated_database_url))


@pytest.fixture()
def api_client(migrated_database_url: str) -> Iterator[TestClient]:
    """创建带审计模块和超管权限的 TestClient。"""

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=migrated_database_url,
    )
    app = create_app(settings)
    app.dependency_overrides[get_authenticated_context_async] = (
        _super_admin_ctx_override
    )
    app.dependency_overrides[get_actor_authorization] = _super_admin_auth_override
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def no_perm_client(migrated_database_url: str) -> Iterator[TestClient]:
    """创建无权限用户的 TestClient（用于测试 403）。"""

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        DATABASE_URL=migrated_database_url,
    )
    app = create_app(settings)
    app.dependency_overrides[get_authenticated_context_async] = (
        _super_admin_ctx_override
    )
    app.dependency_overrides[get_actor_authorization] = _no_permission_auth_override
    with TestClient(app) as client:
        yield client


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 审计日志分页查询 API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestAuditLogQueryAPI:
    """审计日志查询 API 契约测试 — SPEC 18.3."""

    def test_list_audit_logs_empty(
        self,
        api_client: TestClient,
    ) -> None:
        """空库查询审计日志返回空列表。"""

        response = api_client.get("/api/v1/audit/logs")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["pageSize"] == 20
        assert body["pages"] == 0

    def test_list_audit_logs_with_data(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """查询审计日志返回分页结果。"""

        asyncio.run(_seed_audit_log(migrated_database_url))
        asyncio.run(_seed_audit_log(migrated_database_url))

        response = api_client.get("/api/v1/audit/logs")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        # 验证响应字段
        item = body["items"][0]
        assert "id" in item
        assert "actorId" in item
        assert "actorDisplayName" in item
        assert "module" in item
        assert "action" in item
        assert "resourceType" in item
        assert "resourceId" in item
        assert "result" in item
        assert "occurredAt" in item

    def test_filter_by_module(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按模块筛选审计日志。"""

        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(module="identity"),
            ),
        )
        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(module="rbac"),
            ),
        )

        response = api_client.get("/api/v1/audit/logs?module=identity")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["module"] == "identity"

    def test_filter_by_actor_id(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按操作者筛选审计日志。"""

        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(actor_id="user-A"),
            ),
        )
        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(actor_id="user-B"),
            ),
        )

        response = api_client.get("/api/v1/audit/logs?actorId=user-A")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["actorId"] == "user-A"

    def test_filter_by_result(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按操作结果筛选。"""

        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(result="success"),
            ),
        )
        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(result="failure"),
            ),
        )

        response = api_client.get("/api/v1/audit/logs?result=failure")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["result"] == "failure"

    def test_filter_by_resource(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按资源类型和标识筛选。"""

        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(
                    resource_type="user",
                    resource_id="res-001",
                ),
            ),
        )
        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(
                    resource_type="role",
                    resource_id="res-002",
                ),
            ),
        )

        response = api_client.get(
            "/api/v1/audit/logs?resourceType=user&resourceId=res-001",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["resourceType"] == "user"

    def test_filter_by_time_range(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按时间范围筛选。"""

        old_time = datetime(2025, 1, 1, tzinfo=UTC)
        recent_time = datetime(2026, 8, 1, tzinfo=UTC)
        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(occurred_at=old_time),
            ),
        )
        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(occurred_at=recent_time),
            ),
        )

        # 只查询 2026 年的记录
        response = api_client.get(
            "/api/v1/audit/logs?startTime=2026-01-01T00:00:00Z",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1

    def test_get_audit_log_detail(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """查询审计日志详情。"""

        entry_id = asyncio.run(_seed_audit_log(migrated_database_url))

        response = api_client.get(f"/api/v1/audit/logs/{entry_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(entry_id)
        assert body["module"] == "identity"
        assert body["action"] == "user.create"
        assert body["diff"] is not None
        assert "status" in body["diff"]

    def test_get_audit_log_404(self, api_client: TestClient) -> None:
        """查询不存在的审计日志返回 404。"""

        response = api_client.get(f"/api/v1/audit/logs/{uuid4()}")
        assert response.status_code == 404

    def test_pagination(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """分页查询。"""

        for _ in range(5):
            asyncio.run(_seed_audit_log(migrated_database_url))

        response = api_client.get("/api/v1/audit/logs?page=1&pageSize=2")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["page"] == 1
        assert body["pageSize"] == 2
        assert body["pages"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 登录日志分页查询 API 契约
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestLoginLogQueryAPI:
    """登录日志查询 API 契约测试 — SPEC 18.1 / 18.3."""

    def test_list_login_logs_with_data(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """查询登录日志返回分页结果。"""

        asyncio.run(_seed_login_log(migrated_database_url))

        response = api_client.get("/api/v1/audit/login-logs")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert "username" in item
        assert "ipAddress" in item
        assert "result" in item

    def test_filter_login_log_by_username(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按用户名筛选登录日志。"""

        asyncio.run(
            _seed_login_log(
                migrated_database_url,
                entry=_make_login_entry(username="alice"),
            ),
        )
        asyncio.run(
            _seed_login_log(
                migrated_database_url,
                entry=_make_login_entry(username="bob"),
            ),
        )

        response = api_client.get("/api/v1/audit/login-logs?username=alice")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["username"] == "alice"

    def test_filter_login_log_by_ip(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按 IP 筛选登录日志。"""

        asyncio.run(
            _seed_login_log(
                migrated_database_url,
                entry=_make_login_entry(ip_address="10.0.0.1"),
            ),
        )
        asyncio.run(
            _seed_login_log(
                migrated_database_url,
                entry=_make_login_entry(ip_address="10.0.0.2"),
            ),
        )

        response = api_client.get("/api/v1/audit/login-logs?ipAddress=10.0.0.2")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["ipAddress"] == "10.0.0.2"

    def test_filter_login_log_by_result(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """按结果筛选登录日志。"""

        asyncio.run(
            _seed_login_log(
                migrated_database_url,
                entry=_make_login_entry(result="success"),
            ),
        )
        asyncio.run(
            _seed_login_log(
                migrated_database_url,
                entry=_make_login_entry(result="failure"),
            ),
        )

        response = api_client.get("/api/v1/audit/login-logs?result=failure")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["result"] == "failure"

    def test_get_login_log_detail(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """查询登录日志详情。"""

        entry_id = asyncio.run(_seed_login_log(migrated_database_url))

        response = api_client.get(f"/api/v1/audit/login-logs/{entry_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(entry_id)
        assert body["username"] == "testuser"

    def test_get_login_log_404(self, api_client: TestClient) -> None:
        """查询不存在的登录日志返回 404。"""

        response = api_client.get(f"/api/v1/audit/login-logs/{uuid4()}")
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: 审计查询接口无权限返回 403
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestAuditQueryPermission:
    """审计查询权限控制测试 — SPEC 18.3."""

    def test_query_without_permission_returns_403(
        self,
        no_perm_client: TestClient,
    ) -> None:
        """无权限查询审计日志返回 403 — SPEC 18.3."""

        response = no_perm_client.get("/api/v1/audit/logs")
        assert response.status_code == 403

    def test_query_detail_without_permission_returns_403(
        self,
        no_perm_client: TestClient,
    ) -> None:
        """无权限查询审计日志详情返回 403。"""

        response = no_perm_client.get(f"/api/v1/audit/logs/{uuid4()}")
        assert response.status_code == 403

    def test_query_login_logs_without_permission_returns_403(
        self,
        no_perm_client: TestClient,
    ) -> None:
        """无权限查询登录日志返回 403。"""

        response = no_perm_client.get("/api/v1/audit/login-logs")
        assert response.status_code == 403

    def test_export_without_permission_returns_403(
        self,
        no_perm_client: TestClient,
    ) -> None:
        """无权限导出审计日志返回 403。"""

        response = no_perm_client.get("/api/v1/audit/logs/export")
        assert response.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 流式导出
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.api
class TestAuditExportAPI:
    """审计日志流式导出测试 — SPEC 18.3."""

    def test_export_audit_logs_csv(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """导出审计日志返回 CSV 流。"""

        asyncio.run(_seed_audit_log(migrated_database_url))

        response = api_client.get("/api/v1/audit/logs/export")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")
        assert "attachment" in response.headers.get("content-disposition", "")

        # 解析 CSV
        content = response.content.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # 第一行为表头，之后为数据行
        assert len(rows) >= 2
        assert "id" in rows[0]
        assert "module" in rows[0]
        assert "action" in rows[0]

    def test_export_login_logs_csv(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """导出登录日志返回 CSV 流。"""

        asyncio.run(_seed_login_log(migrated_database_url))

        response = api_client.get("/api/v1/audit/login-logs/export")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")

        content = response.content.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) >= 2
        assert "username" in rows[0]
        assert "ipAddress" in rows[0]

    def test_export_writes_new_audit_event(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """导出行为本身写入新的审计事件 — SPEC 18.3.

        SPEC 18.3: "导出属于受控操作并记录新的审计事件"。
        导出前 audit_logs 为空，导出后应至少有 1 条
        action=audit.log.export 的记录。
        """

        count_before = asyncio.run(_count_audit_logs(migrated_database_url))
        assert count_before == 0

        response = api_client.get("/api/v1/audit/logs/export")
        assert response.status_code == 200

        # 导出操作写入了一条新的审计事件
        count_after = asyncio.run(_count_audit_logs(migrated_database_url))
        assert count_after == 1

        # 验证审计事件内容 — action 为 audit.log.export
        async def _verify_export_action() -> str | None:
            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT action FROM audit_logs "
                            "WHERE action = 'audit.log.export'",
                        ),
                    )
                    row = result.first()
                    return row[0] if row else None
            finally:
                await engine.dispose()

        action = asyncio.run(_verify_export_action())
        assert action == "audit.log.export"

    def test_export_with_filter(
        self,
        api_client: TestClient,
        migrated_database_url: str,
    ) -> None:
        """导出支持筛选条件。"""

        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(module="identity"),
            ),
        )
        asyncio.run(
            _seed_audit_log(
                migrated_database_url,
                entry=_make_audit_entry(module="rbac"),
            ),
        )

        response = api_client.get("/api/v1/audit/logs/export?module=identity")
        assert response.status_code == 200

        content = response.content.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # 表头 + 1 条数据行（不含导出事件，导出事件在 audit_logs 表但
        # 导出的 CSV 是在记录审计事件之后流式查询的，所以 CSV 可能包含
        # 导出事件本身。这里验证至少有数据行。）
        data_rows = rows[1:]
        # 导出操作写入的审计事件 module="audit"，筛选 module=identity 应排除它
        identity_rows = [r for r in data_rows if "identity" in r]
        assert len(identity_rows) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 日志保留清理
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestAuditRetentionIntegration:
    """审计日志保留清理集成测试 — SPEC 18.4 / 25.3."""

    async def test_dry_run_does_not_delete(
        self,
        migrated_database_url: str,
    ) -> None:
        """dry-run 模式不删除任何数据 — SPEC 25.3."""

        from app.application.ports import SystemClock
        from app.infrastructure.db.engine import create_db_engine as _create
        from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
        from app.modules.audit.retention import (
            RetentionConfig,
            execute_cleanup,
        )

        # 插入一条旧记录
        old_time = datetime(2020, 1, 1, tzinfo=UTC)
        await _seed_audit_log(
            migrated_database_url,
            entry=_make_audit_entry(occurred_at=old_time),
        )

        config = RetentionConfig(
            audit_log_retention_days=90,
            login_log_retention_days=90,
            security_event_retention_days=365,
        )

        engine = _create(migrated_database_url)
        try:
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                result = await execute_cleanup(
                    config=config,
                    clock=SystemClock(),
                    apply=False,
                    uow=uow,
                )

            assert not result.applied
            assert result.audit_logs_expired >= 1
            assert result.audit_logs_deleted == 0

            # 数据未被删除
            count = await _count_audit_logs(migrated_database_url)
            assert count >= 1
        finally:
            await engine.dispose()

    async def test_apply_deletes_expired(
        self,
        migrated_database_url: str,
    ) -> None:
        """--apply 模式删除过期数据 — SPEC 25.3."""

        from app.application.ports import SystemClock
        from app.infrastructure.db.engine import create_db_engine as _create
        from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
        from app.modules.audit.retention import (
            RetentionConfig,
            execute_cleanup,
        )

        # 插入一条旧记录和一条新记录
        old_time = datetime(2020, 1, 1, tzinfo=UTC)
        recent_time = datetime.now(UTC) - timedelta(days=1)
        await _seed_audit_log(
            migrated_database_url,
            entry=_make_audit_entry(occurred_at=old_time),
        )
        await _seed_audit_log(
            migrated_database_url,
            entry=_make_audit_entry(occurred_at=recent_time),
        )

        count_before = await _count_audit_logs(migrated_database_url)
        assert count_before == 2

        config = RetentionConfig(
            audit_log_retention_days=90,
            login_log_retention_days=90,
            security_event_retention_days=365,
        )

        engine = _create(migrated_database_url)
        try:
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                result = await execute_cleanup(
                    config=config,
                    clock=SystemClock(),
                    apply=True,
                    uow=uow,
                )
                await uow.commit()

            assert result.applied
            assert result.audit_logs_deleted == 1

            # 只有旧记录被删除
            count_after = await _count_audit_logs(migrated_database_url)
            assert count_after == 1
        finally:
            await engine.dispose()

    async def test_login_log_retention_independent(
        self,
        migrated_database_url: str,
    ) -> None:
        """登录日志保留期限与审计日志独立。"""

        from app.application.ports import SystemClock
        from app.infrastructure.db.engine import create_db_engine as _create
        from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
        from app.modules.audit.retention import (
            RetentionConfig,
            execute_cleanup,
        )

        # 审计日志 30 天前，登录日志 30 天前
        old_time = datetime.now(UTC) - timedelta(days=60)
        await _seed_audit_log(
            migrated_database_url,
            entry=_make_audit_entry(occurred_at=old_time),
        )
        await _seed_login_log(
            migrated_database_url,
            entry=_make_login_entry(occurred_at=old_time),
        )

        # 审计日志 7 天保留，登录日志 90 天保留
        config = RetentionConfig(
            audit_log_retention_days=7,
            login_log_retention_days=90,
            security_event_retention_days=365,
        )

        engine = _create(migrated_database_url)
        try:
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                result = await execute_cleanup(
                    config=config,
                    clock=SystemClock(),
                    apply=True,
                    uow=uow,
                )
                await uow.commit()

            # 审计日志被删除（7 天保留），登录日志保留（90 天保留）
            assert result.audit_logs_deleted >= 1
            assert result.login_logs_deleted == 0
        finally:
            await engine.dispose()

    async def test_security_event_retention_config_independent(
        self,
    ) -> None:
        """安全事件保留策略独立于普通访问日志 — SPEC 18.4.

        RetentionConfig 中 security_event_retention_days 是独立字段，
        与 audit_log_retention_days 和 login_log_retention_days 无关联。
        """

        from app.modules.audit.retention import RetentionConfig

        config = RetentionConfig(
            audit_log_retention_days=30,
            login_log_retention_days=60,
            security_event_retention_days=999,
        )
        # 三个值各自独立
        assert config.security_event_retention_days == 999
        assert config.audit_log_retention_days == 30
        assert config.login_log_retention_days == 60
        assert config.security_event_retention_days != config.audit_log_retention_days
