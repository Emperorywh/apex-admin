"""用户模块集成测试 — SPEC 5.6 / 5.7 / 11.1 / 11.3 / 18.2 / 28.2.

覆盖验收标准:
  - AC-2: 重置密码与禁用用例发布事务内领域事件，
          事件与业务同回滚（集成测试）。
  - AC-4: 用户状态与资料变更通过 AuditPort 写审计且与业务同事务。
  - AC-3: 已产生审计记录的用户物理删除被拒绝。

使用真实 PostgreSQL（Testcontainers / 本地二进制），禁止 SQLite（SPEC 28.2）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.application.ports import Clock, IdGenerator
from app.core.events.handlers import TransactionalEventHandler
from app.core.security.password import Argon2Hasher
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.errors import (
    UserAlreadyDisabledError,
    UserHasAuditRecordsError,
    UserInvalidOldPasswordError,
    UserNotFoundError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.events.events import DomainEvent

# ── 迁移辅助 ───────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

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
    """清理用户和审计表。"""
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM users"))
    finally:
        await engine.dispose()


async def _count_users(database_url: str) -> int:
    """查询 users 行数。"""
    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM users"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _count_audit_logs(database_url: str) -> int:
    """查询 audit_logs 行数。"""
    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM audit_logs"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _get_user_status(database_url: str, user_id: UUID) -> str:
    """查询用户状态。"""
    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT status FROM users WHERE id = :id"),
                {"id": str(user_id)},
            )
            row = result.first()
            return row[0] if row else ""
    finally:
        await engine.dispose()


async def _get_audit_for_resource(
    database_url: str,
    resource_id: str,
) -> list[dict[str, object]]:
    """查询指定资源的审计记录。"""
    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT action, resource_id, diff FROM audit_logs "
                    "WHERE resource_id = :rid ORDER BY occurred_at",
                ),
                {"rid": resource_id},
            )
            return [
                {"action": row[0], "resource_id": row[1], "diff": row[2]}
                for row in result
            ]
    finally:
        await engine.dispose()


# ── 测试用辅助 ──────────────────────────────────────────────────────────────


class FixedClock(Clock):
    """固定时钟。"""

    def __init__(self, time: datetime) -> None:
        self._time = time

    def now(self) -> datetime:
        return self._time


class FixedIdGenerator(IdGenerator):
    """固定 ID 生成器——依次返回预设 UUID。"""

    def __init__(self, *ids: UUID) -> None:
        self._ids = list(ids)
        self._n = 0

    def generate_id(self) -> UUID:
        if self._n < len(self._ids):
            result = self._ids[self._n]
            self._n += 1
            return result
        return uuid4()


class FailingUserDisabledHandler(TransactionalEventHandler):
    """故意失败的事件处理器——验证禁用事件事务回滚（SPEC 5.7）。"""

    @property
    def code(self) -> str:
        return "AUTH.FAILING_DISABLE_HANDLER"

    @property
    def event_code(self) -> str:
        return "USER.DISABLED"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        raise RuntimeError("禁用事件处理器故意失败，验证事务回滚")


class FailingPasswordResetHandler(TransactionalEventHandler):
    """故意失败的事件处理器——验证重置密码事件事务回滚（SPEC 5.7）。"""

    @property
    def code(self) -> str:
        return "AUTH.FAILING_RESET_HANDLER"

    @property
    def event_code(self) -> str:
        return "USER.PASSWORD_RESET_BY_ADMIN"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        raise RuntimeError("重置密码事件处理器故意失败，验证事务回滚")


class RecordingUserDisabledHandler(TransactionalEventHandler):
    """记录禁用事件——验证事件被发布。"""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    @property
    def code(self) -> str:
        return "AUTH.RECORD_DISABLE"

    @property
    def event_code(self) -> str:
        return "USER.DISABLED"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        self.events.append(event)


class RecordingPasswordResetHandler(TransactionalEventHandler):
    """记录重置密码事件——验证事件被发布。"""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    @property
    def code(self) -> str:
        return "AUTH.RECORD_RESET"

    @property
    def event_code(self) -> str:
        return "USER.PASSWORD_RESET_BY_ADMIN"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        self.events.append(event)


def _make_use_case(  # type: ignore[no-untyped-def]
    engine,
    *,
    user_ids: tuple[UUID, ...] | None = None,
    event_handlers: list[TransactionalEventHandler] | None = None,
):
    """构造测试用 UserUseCase。"""
    from app.application.context import UseCaseContext
    from app.modules.audit.adapter import SqlAlchemyAuditRepository
    from app.modules.identity.use_case import UserUseCase

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):
        return SqlAlchemyAuditRepository(session)

    ids = user_ids or (uuid4(),)
    return (
        UserUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
            id_generator=FixedIdGenerator(*ids),
            hasher=Argon2Hasher(),
            event_handlers=event_handlers or [],
            audit_factory=audit_factory,
        ),
        UseCaseContext(request_id="test-req", actor_id="admin-001"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 事件与业务同回滚
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_disable_event_published_in_transaction(database_url: str) -> None:
    """禁用用例发布 USER.DISABLED 事件 — SPEC 5.7 / 11.1.

    验证: 禁用用户后事件被事务内处理器接收，
    且事件载荷只含稳定编码和 ID。
    """

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.models import UserStatus
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)
        user_id = uuid4()
        handler = RecordingUserDisabledHandler()

        create_uc, ctx = _make_use_case(
            engine,
            user_ids=(user_id,),
            event_handlers=[],
        )
        disable_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),  # 审计 ID
            event_handlers=[handler],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )

            # 验证初始状态为 active
            status_before = await _get_user_status(database_url, user_id)
            assert status_before == UserStatus.ACTIVE.value

            await disable_uc.disable_user(ctx, user_id)

            # 事件被发布
            assert len(handler.events) == 1
            event = handler.events[0]
            assert event.code == "USER.DISABLED"
            assert event.user_id == str(user_id)

            # 状态变更为 disabled
            status_after = await _get_user_status(database_url, user_id)
            assert status_after == UserStatus.DISABLED.value
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_disable_event_failure_rolls_back(database_url: str) -> None:
    """禁用事件处理器失败时整体回滚 — SPEC 5.7 / 11.1.

    SPEC 5.7: "任一事务内处理器失败时，整个 Use Case 回滚"。
    验证: 禁用用户后事件处理器失败，用户状态不变且无审计记录。
    """

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.models import UserStatus
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        disable_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[FailingUserDisabledHandler()],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )

            audit_before = await _count_audit_logs(database_url)

            with pytest.raises(RuntimeError, match="故意失败"):
                await disable_uc.disable_user(ctx, user_id)

            # 用户状态仍为 active（回滚）
            status = await _get_user_status(database_url, user_id)
            assert status == UserStatus.ACTIVE.value

            # 无新增审计记录（审计也回滚）
            audit_after = await _count_audit_logs(database_url)
            assert audit_after == audit_before
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_password_reset_event_published_in_transaction(
    database_url: str,
) -> None:
    """重置密码用例发布 USER.PASSWORD_RESET_BY_ADMIN 事件 — SPEC 5.7 / 11.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import (
            UserCreateRequest,
            UserResetPasswordRequest,
        )

        engine = create_db_engine(database_url)
        user_id = uuid4()
        handler = RecordingPasswordResetHandler()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        reset_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[handler],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )

            await reset_uc.reset_password(
                ctx,
                user_id,
                UserResetPasswordRequest(new_password="new_secure_password_12"),
            )

            assert len(handler.events) == 1
            event = handler.events[0]
            assert event.code == "USER.PASSWORD_RESET_BY_ADMIN"
            assert event.user_id == str(user_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_password_reset_event_failure_rolls_back(
    database_url: str,
) -> None:
    """重置密码事件处理器失败时整体回滚 — SPEC 5.7 / 11.1.

    验证: 重置密码后事件处理器失败，密码不变且无审计记录。
    """

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import (
            UserCreateRequest,
            UserResetPasswordRequest,
        )

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        reset_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[FailingPasswordResetHandler()],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )

            audit_before = await _count_audit_logs(database_url)

            with pytest.raises(RuntimeError, match="故意失败"):
                await reset_uc.reset_password(
                    ctx,
                    user_id,
                    UserResetPasswordRequest(
                        new_password="new_secure_password_12",
                    ),
                )

            # 无新增审计记录（审计也回滚）
            audit_after = await _count_audit_logs(database_url)
            assert audit_after == audit_before
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: 审计与业务同事务
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_create_user_writes_audit_same_transaction(
    database_url: str,
) -> None:
    """创建用户时写审计，与业务同事务提交 — SPEC 5.7 / 18.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)
        user_id = uuid4()

        uc, ctx = _make_use_case(engine, user_ids=(user_id,))

        try:
            await uc.create_user(
                ctx,
                UserCreateRequest(
                    username="alice",
                    display_name="Alice",
                    password="secure_password_12",
                ),
            )

            # 用户和审计同时存在
            assert await _count_users(database_url) == 1
            assert await _count_audit_logs(database_url) == 1

            audits = await _get_audit_for_resource(database_url, str(user_id))
            assert len(audits) == 1
            assert audits[0]["action"] == "identity.user.create"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_disable_writes_audit_with_status_diff(
    database_url: str,
) -> None:
    """禁用用户审计记录包含状态变更差异 — SPEC 18.2 / 5.7."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        disable_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )
            await disable_uc.disable_user(ctx, user_id)

            audits = await _get_audit_for_resource(database_url, str(user_id))
            disable_audits = [a for a in audits if "disable" in a["action"]]
            assert len(disable_audits) == 1

            # diff 包含 status 变更
            diff = disable_audits[0]["diff"]
            assert diff is not None
            assert "status" in diff
            assert diff["status"]["old"] == "active"
            assert diff["status"]["new"] == "disabled"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_audit_diff_excludes_password(database_url: str) -> None:
    """审计差异不包含 password_hash — SPEC 18.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import (
            UserCreateRequest,
            UserUpdateRequest,
        )

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        update_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )
            await update_uc.update_user(
                ctx,
                user_id,
                UserUpdateRequest(
                    display_name="Bobby",
                    phone=None,
                    email=None,
                ),
            )

            audits = await _get_audit_for_resource(database_url, str(user_id))
            update_audits = [a for a in audits if "update" in a["action"]]
            assert len(update_audits) == 1
            diff = update_audits[0]["diff"]
            assert diff is not None
            # diff 不含 password_hash
            assert "password_hash" not in diff
            assert "password" not in diff
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 物理删除审计保护
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_delete_user_with_audit_records_rejected(
    database_url: str,
) -> None:
    """已产生审计记录的用户物理删除被拒绝 — SPEC 11.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        delete_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[],
        )

        try:
            # 创建用户（同时产生审计记录）
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )

            # 有审计记录，物理删除被拒绝
            with pytest.raises(UserHasAuditRecordsError):
                await delete_uc.delete_user(ctx, user_id)

            # 用户仍存在
            assert await _count_users(database_url) == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_delete_user_without_audit_succeeds(database_url: str) -> None:
    """无审计记录的用户可物理删除 — SPEC 11.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        delete_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )

            # 清除审计记录（模拟无审计场景）
            cleanup_engine = create_db_engine(database_url)
            try:
                async with cleanup_engine.begin() as conn:
                    await conn.execute(text("DELETE FROM audit_logs"))
            finally:
                await cleanup_engine.dispose()

            # 无审计记录，物理删除成功
            await delete_uc.delete_user(ctx, user_id)
            assert await _count_users(database_url) == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# 自助改密旧密码校验
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_self_change_password_validates_old_password(
    database_url: str,
) -> None:
    """自助改密校验旧密码——错误旧密码返回异常 — SPEC 11.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import (
            SelfChangePasswordRequest,
            UserCreateRequest,
        )

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )

            # 自助改密（需要正确的旧密码）
            self_uc, self_ctx = _make_use_case(
                engine,
                user_ids=(uuid4(),),
                event_handlers=[],
            )
            # 覆盖 ctx 使 actor_id 指向当前用户
            from app.application.context import UseCaseContext

            self_ctx = UseCaseContext(
                request_id="self-req",
                actor_id=str(user_id),
            )

            # 错误旧密码
            with pytest.raises(UserInvalidOldPasswordError):
                await self_uc.change_self_password(
                    self_ctx,
                    SelfChangePasswordRequest(
                        old_password="wrong_password_12",
                        new_password="new_secure_password_12",
                    ),
                )

            # 正确旧密码
            await self_uc.change_self_password(
                self_ctx,
                SelfChangePasswordRequest(
                    old_password="secure_password_12",
                    new_password="new_secure_password_12",
                ),
            )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# 状态冲突
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_disable_already_disabled_raises(database_url: str) -> None:
    """禁用已禁用用户返回冲突 — SPEC 11.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)
        user_id = uuid4()

        create_uc, ctx = _make_use_case(engine, user_ids=(user_id,))
        disable_uc, _ = _make_use_case(
            engine,
            user_ids=(uuid4(),),
            event_handlers=[],
        )

        try:
            await create_uc.create_user(
                ctx,
                UserCreateRequest(
                    username="bob",
                    display_name="Bob",
                    password="secure_password_12",
                ),
            )
            await disable_uc.disable_user(ctx, user_id)

            # 再次禁用 → 冲突
            with pytest.raises(UserAlreadyDisabledError):
                await disable_uc.disable_user(ctx, user_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_get_nonexistent_user_raises_not_found(
    database_url: str,
) -> None:
    """查询不存在用户返回 404 — SPEC 10.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        uc, ctx = _make_use_case(engine)

        try:
            with pytest.raises(UserNotFoundError):
                await uc.get_user(ctx, uuid4())
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# 用户名唯一冲突
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_duplicate_username_raises_conflict(database_url: str) -> None:
    """重复用户名触发唯一约束冲突 — SPEC 8.3 / 8.4."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.identity.errors import UserAlreadyExistsError
        from app.modules.identity.schemas import UserCreateRequest

        engine = create_db_engine(database_url)

        uc, ctx = _make_use_case(
            engine,
            user_ids=(uuid4(), uuid4()),
        )

        try:
            await uc.create_user(
                ctx,
                UserCreateRequest(
                    username="alice",
                    display_name="Alice",
                    password="secure_password_12",
                ),
            )

            with pytest.raises(UserAlreadyExistsError):
                await uc.create_user(
                    ctx,
                    UserCreateRequest(
                        username="alice",
                        display_name="Alice 2",
                        password="secure_password_12",
                    ),
                )

            # 只有一行
            assert await _count_users(database_url) == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# Router 不导入 AsyncSession/Repository — SPEC 5.2 / 5.6
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestRouterArchitecture:
    """Router 架构边界 — SPEC 5.2 / 5.6.

    SPEC 5.2: "禁止路由层直接访问数据库"。
    SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository"。
    通过 AST 静态扫描验证 Router 模块不导入禁止类型。
    """

    def test_router_not_import_asyncsession(self) -> None:
        """Router 模块不导入 AsyncSession。"""

        import ast

        router_path = "src/app/modules/identity/router.py"
        from pathlib import Path

        source = Path(router_path).read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_names = {"AsyncSession", "SqlAlchemyUserRepository", "session"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"Router 不应导入 {alias.name}"
                    )

    def test_router_has_no_direct_db_access(self) -> None:
        """Router 源码不包含直接数据库访问模式。"""

        from pathlib import Path

        source = Path("src/app/modules/identity/router.py").read_text(
            encoding="utf-8",
        )
        # 不直接引用 session.commit / session.execute 等
        assert "session.commit" not in source
        assert "session.execute" not in source
        assert "session.add" not in source
