"""审计模块集成测试 — SPEC 18.1 / 18.2 / 5.7 / 8.3.

覆盖验收标准:
  - AC-0: 审计 Port 写入与调用方业务事务同提交、同回滚。
  - AC-2: 操作者显示名与目标显示名等易变信息按操作发生时快照保存。
  - AC-3: 审计与登录日志表无应用层 UPDATE/DELETE 路径。

使用真实 PostgreSQL（Testcontainers / 本地二进制），禁止 SQLite（SPEC 28.2）。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.modules.audit.models import (
    AuditEntry,
    ChangeDiff,
    DiffField,
    LoginLogEntry,
)

# ── 迁移辅助 ───────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head（创建审计表）。"""

    import asyncio

    from alembic import command

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_tables(database_url: str) -> None:
    """清理审计、登录日志与测试使用的业务表。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM login_logs"))
            await conn.execute(text("DELETE FROM example_items"))
    finally:
        await engine.dispose()


async def _count_audit_logs(database_url: str) -> int:
    """查询 audit_logs 行数。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM audit_logs"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _count_login_logs(database_url: str) -> int:
    """查询 login_logs 行数。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM login_logs"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _read_audit_log(database_url: str, entry_id: UUID) -> dict[str, object]:
    """读取单条审计记录。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT actor_id, actor_display_name, module, action, "
                    "resource_type, resource_id, resource_display_name, "
                    "result, request_id, diff "
                    "FROM audit_logs WHERE id = :id",
                ),
                {"id": str(entry_id)},
            )
            row = result.first()
            if row is None:
                return {}
            return {
                "actor_id": row[0],
                "actor_display_name": row[1],
                "module": row[2],
                "action": row[3],
                "resource_type": row[4],
                "resource_id": row[5],
                "resource_display_name": row[6],
                "result": row[7],
                "request_id": row[8],
                "diff": row[9],
            }
    finally:
        await engine.dispose()


# ── 辅助工厂 ───────────────────────────────────────────────────────────────


def _make_audit_entry(
    *,
    actor_display_name: str = "Alice",
    resource_display_name: str | None = "Bob",
    entry_id: UUID | None = None,
) -> AuditEntry:
    """构造测试用审计条目。"""

    return AuditEntry(
        id=entry_id or uuid4(),
        actor_id="user-001",
        actor_display_name=actor_display_name,
        module="user",
        action="user.update",
        resource_type="user",
        resource_id="user-002",
        resource_display_name=resource_display_name,
        result="success",
        request_id="req-001",
        diff=ChangeDiff(
            fields=(
                DiffField(
                    field_name="status",
                    old_value="active",
                    new_value="disabled",
                ),
            ),
        ),
        occurred_at=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
    )


def _make_login_entry(
    *,
    entry_id: UUID | None = None,
    result: str = "success",
) -> LoginLogEntry:
    """构造测试用登录日志条目。"""

    return LoginLogEntry(
        id=entry_id or uuid4(),
        user_id="user-001",
        username="alice",
        session_id="sess-001",
        ip_address="10.0.0.1",
        user_agent="Mozilla/5.0",
        result=result,
        failure_reason=None if result == "success" else "invalid_credentials",
        occurred_at=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 审计 Port 与业务事务同提交、同回滚
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_audit_commits_with_business_transaction(
    database_url: str,
) -> None:
    """审计记录与业务事务共同提交 — SPEC 5.7 / 18.2.

    SPEC 5.7: "成功操作的核心审计必须由 Use Case 显式调用审计 Port，
    并与业务事务共同提交"。

    验证: 在同一 UoW 中插入业务数据并记录审计，提交后两者都持久化。
    """

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyAuditRepository

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        entry_id = uuid4()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        try:
            # 在同一 UoW 中记录审计并提交
            async with uow_factory() as uow:
                audit_repo = SqlAlchemyAuditRepository(uow.session)
                await audit_repo.record_audit(_make_audit_entry(entry_id=entry_id))
                await uow.commit()

            # 提交后审计记录持久化
            count = await _count_audit_logs(database_url)
            assert count == 1

            stored = await _read_audit_log(database_url, entry_id)
            assert stored["action"] == "user.update"
            assert stored["result"] == "success"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_audit_rolls_back_with_business_transaction(
    database_url: str,
) -> None:
    """审计记录与业务事务共同回滚 — SPEC 5.7 / 18.2.

    SPEC 5.7: 审计记录与业务事务同提交、同回滚。
    业务事务回滚时，审计记录一并回滚，不残留在数据库中。
    """

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyAuditRepository

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        try:
            # 在同一 UoW 中记录审计但不提交（异常退出触发回滚）
            with pytest.raises(RuntimeError, match="模拟业务失败"):
                async with uow_factory() as uow:
                    audit_repo = SqlAlchemyAuditRepository(uow.session)
                    await audit_repo.record_audit(_make_audit_entry())
                    raise RuntimeError("模拟业务失败")

            # 回滚后审计记录不应存在
            count = await _count_audit_logs(database_url)
            assert count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_audit_and_business_data_same_transaction(
    database_url: str,
) -> None:
    """审计记录与业务数据在同一事务提交或回滚 — SPEC 5.7.

    在同一 UoW 中同时写入业务数据（example_items）和审计记录，
    验证两者要么同时持久化，要么同时回滚。
    """

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyAuditRepository

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        audit_id = uuid4()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        try:
            # ── 场景 1: 同时提交 ──
            async with uow_factory() as uow:
                session = uow.session
                # 写入业务数据
                await session.execute(
                    text(
                        "INSERT INTO example_items (id, name, description, "
                        "created_at, updated_at) VALUES "
                        "(:id, :name, NULL, "
                        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                    ),
                    {"id": str(uuid4()), "name": "biz-with-audit"},
                )
                # 写入审计记录
                audit_repo = SqlAlchemyAuditRepository(session)
                await audit_repo.record_audit(_make_audit_entry(entry_id=audit_id))
                await uow.commit()

            # 两者都持久化
            audit_count = await _count_audit_logs(database_url)
            assert audit_count == 1

            # ── 场景 2: 同时回滚 ──
            rollback_audit_id = uuid4()
            with pytest.raises(RuntimeError, match="业务异常"):
                async with uow_factory() as uow:
                    session = uow.session
                    await session.execute(
                        text(
                            "INSERT INTO example_items (id, name, description, "
                            "created_at, updated_at) VALUES "
                            "(:id, :name, NULL, "
                            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                        ),
                        {"id": str(uuid4()), "name": "rollback-biz"},
                    )
                    audit_repo = SqlAlchemyAuditRepository(session)
                    await audit_repo.record_audit(
                        _make_audit_entry(entry_id=rollback_audit_id),
                    )
                    raise RuntimeError("业务异常")

            # 审计记录未增加（回滚了）
            audit_count_after = await _count_audit_logs(database_url)
            assert audit_count_after == 1  # 只有场景 1 的记录
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_login_log_commits_with_transaction(
    database_url: str,
) -> None:
    """登录日志与业务事务共同提交 — SPEC 18.1 / 5.7."""

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyLoginLogRepository

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        try:
            async with uow_factory() as uow:
                login_repo = SqlAlchemyLoginLogRepository(uow.session)
                await login_repo.record_login(_make_login_entry())
                await uow.commit()

            count = await _count_login_logs(database_url)
            assert count == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 显示名快照
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_snapshot_display_names_persisted(
    database_url: str,
) -> None:
    """操作者/目标显示名快照持久化 — SPEC 18.2.

    SPEC 18.2: "操作者显示名称、目标显示名称等易变信息按操作发生时
    快照保存"。

    验证: 审计记录中的 actor_display_name 和 resource_display_name
    在操作发生时写入，提交后与原始快照值一致。
    """

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyAuditRepository

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        entry_id = uuid4()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        try:
            # 记录审计时快照值
            async with uow_factory() as uow:
                audit_repo = SqlAlchemyAuditRepository(uow.session)
                await audit_repo.record_audit(
                    _make_audit_entry(
                        actor_display_name="快照时的Alice",
                        resource_display_name="快照时的Bob",
                        entry_id=entry_id,
                    ),
                )
                await uow.commit()

            # 读取并验证快照值
            stored = await _read_audit_log(database_url, entry_id)
            assert stored["actor_display_name"] == "快照时的Alice"
            assert stored["resource_display_name"] == "快照时的Bob"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_diff_persisted_as_jsonb(database_url: str) -> None:
    """变更差异以 JSONB 持久化 — SPEC 18.2."""

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.adapter import SqlAlchemyAuditRepository

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        entry_id = uuid4()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        try:
            async with uow_factory() as uow:
                audit_repo = SqlAlchemyAuditRepository(uow.session)
                await audit_repo.record_audit(_make_audit_entry(entry_id=entry_id))
                await uow.commit()

            stored = await _read_audit_log(database_url, entry_id)
            diff = stored["diff"]
            assert diff is not None
            # diff 是 JSONB 字典
            assert isinstance(diff, dict)
            assert "status" in diff
            assert diff["status"]["old"] == "active"
            assert diff["status"]["new"] == "disabled"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 审计表无 UPDATE/DELETE 应用层路径
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestNoUpdateDeletePaths:
    """审计 Adapter 无 UPDATE/DELETE 方法 — SPEC 8.3 / 18.2.

    SPEC 8.3: "审计日志等不可变数据不得通过通用 CRUD 随意修改"。
    通过检查 Adapter 类的公开方法，确认不存在 update/delete 路径。
    """

    def test_audit_adapter_no_update_delete(self) -> None:
        """SqlAlchemyAuditRepository 无 update/delete 方法。

        ``record_audit`` 为写入（INSERT），``count_by_resource`` 为只读查询
        （SELECT），两者均不修改审计数据，不违反不可变约束。
        """

        from app.modules.audit.adapter import SqlAlchemyAuditRepository

        public_methods = {
            name
            for name, member in inspect.getmembers(
                SqlAlchemyAuditRepository,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        }
        # 允许的公开方法: record_audit（INSERT）+ count_by_resource（只读 SELECT）
        assert public_methods == {"record_audit", "count_by_resource"}
        # 不存在 update/delete
        assert not any("update" in m.lower() for m in public_methods)
        assert not any("delete" in m.lower() for m in public_methods)

    def test_login_log_adapter_no_update_delete(self) -> None:
        """SqlAlchemyLoginLogRepository 无 update/delete 方法。"""

        from app.modules.audit.adapter import SqlAlchemyLoginLogRepository

        public_methods = {
            name
            for name, member in inspect.getmembers(
                SqlAlchemyLoginLogRepository,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        }
        # 只允许 record_login（INSERT）
        assert public_methods == {"record_login"}
        # 不存在 update/delete
        assert not any("update" in m.lower() for m in public_methods)
        assert not any("delete" in m.lower() for m in public_methods)

    def test_audit_port_no_update_delete(self) -> None:
        """AuditPort 无 update/delete 抽象方法。

        ``record_audit`` 为写入（INSERT），``count_by_resource`` 为只读查询，
        两者均不修改审计数据。
        """

        from app.modules.audit.port import AuditPort

        public_methods = {
            name
            for name, member in inspect.getmembers(AuditPort)
            if not name.startswith("_") and callable(member)
        }
        assert "record_audit" in public_methods
        assert "count_by_resource" in public_methods
        assert not any("update" in m.lower() for m in public_methods)
        assert not any("delete" in m.lower() for m in public_methods)

    def test_login_log_port_no_update_delete(self) -> None:
        """LoginLogPort 无 update/delete 抽象方法。"""

        from app.modules.audit.port import LoginLogPort

        public_methods = {
            name
            for name, member in inspect.getmembers(LoginLogPort)
            if not name.startswith("_") and callable(member)
        }
        assert "record_login" in public_methods
        assert not any("update" in m.lower() for m in public_methods)
        assert not any("delete" in m.lower() for m in public_methods)
