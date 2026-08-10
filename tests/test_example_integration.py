"""示例模块集成测试 — SPEC 5.6 / 5.7 / 28.2.

覆盖验收标准:
  - AC-2: 示例写 Use Case 成功只提交一次（集成测试）。
  - AC-2: 事件处理器失败整体回滚（集成测试）。

SPEC 5.6: 一个最外层写 Use Case 对应一个 Unit of Work。
SPEC 5.7: 任一事务内处理器失败时，整个 Use Case 回滚。

使用真实 PostgreSQL（Testcontainers / 本地二进制），禁止 SQLite（SPEC 28.2）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.application.ports import Clock, IdGenerator
from app.core.events.handlers import TransactionalEventHandler
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.example.handler import ExampleItemCreatedHandler
from app.modules.example.use_case import ExampleItemUseCase

if TYPE_CHECKING:
    from app.core.events.events import DomainEvent

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


# ── 迁移辅助 ───────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head（创建 example_items 表）。"""

    import asyncio

    from alembic import command

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_table(database_url: str) -> None:
    """清理 example_items 表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM example_items"))
    finally:
        await engine.dispose()


async def _count_items(database_url: str) -> int:
    """查询 example_items 行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM example_items"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


# ── 测试用 Clock 和 IdGenerator ────────────────────────────────────────────


class FixedClock(Clock):
    """固定时钟 — 返回预设时间，便于测试确定性。"""

    def __init__(self, time: datetime) -> None:
        self._time = time

    def now(self) -> datetime:
        return self._time


class FixedIdGenerator(IdGenerator):
    """固定 ID 生成器 — 返回预设 UUID，便于测试断言。"""

    def __init__(self, item_id) -> None:
        self._id = item_id
        self._first = True

    def generate_id(self):
        if self._first:
            self._first = False
            return self._id
        from uuid import uuid4

        return uuid4()


# ── 失败事件处理器 ─────────────────────────────────────────────────────────


class FailingExampleHandler(TransactionalEventHandler):
    """故意失败的事件处理器 — 验证事务回滚（SPEC 5.7）。"""

    @property
    def code(self) -> str:
        return "EXAMPLE.FAILING_HANDLER"

    @property
    def event_code(self) -> str:
        return "EXAMPLE.ITEM_CREATED"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        raise RuntimeError("事件处理器故意失败，验证事务回滚")


# ── 集成测试 ─────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.integration
async def test_create_item_commits_once(database_url: str) -> None:
    """写 Use Case 成功只提交一次（AC-2）。

    SPEC 5.6: 成功路径恰好提交一次。
    验证: 创建条目后数据库中恰好一行，且事件处理器在事务内执行
    （描述被处理器更新为 ``[processed]``）。
    """

    from datetime import UTC, datetime
    from uuid import UUID

    await _apply_migrations(database_url)
    await _cleanup_table(database_url)
    try:
        from app.application.context import UseCaseContext
        from app.modules.example.schemas import ExampleItemCreateRequest

        engine = create_db_engine(database_url)

        fixed_id = UUID("11111111-1111-1111-1111-111111111111")
        fixed_time = datetime(2026, 1, 1, tzinfo=UTC)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        use_case = ExampleItemUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(fixed_time),
            id_generator=FixedIdGenerator(fixed_id),
            event_handlers=[ExampleItemCreatedHandler()],
        )

        ctx = UseCaseContext(request_id="test-req")
        request = ExampleItemCreateRequest(
            name="integration-test",
            description="original",
        )

        try:
            result = await use_case.create_item(ctx, request)

            # 响应正确
            assert result.name == "integration-test"

            # 数据库恰好一行
            count = await _count_items(database_url)
            assert count == 1

            # 事件处理器在事务内执行 — 描述被更新为 [processed]
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT name, description FROM example_items LIMIT 1"),
                    )
                ).first()
                assert row is not None
                assert row[0] == "integration-test"
                assert row[1] == "[processed]"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_event_handler_failure_rolls_back(database_url: str) -> None:
    """事件处理器失败时整体回滚（AC-2）。

    SPEC 5.7: "任一事务内处理器失败时，整个 Use Case 回滚"。
    验证: 创建条目后事件处理器失败，数据库中无任何持久化数据。
    """

    from datetime import UTC, datetime
    from uuid import UUID

    await _apply_migrations(database_url)
    await _cleanup_table(database_url)
    try:
        from app.application.context import UseCaseContext
        from app.modules.example.schemas import ExampleItemCreateRequest

        engine = create_db_engine(database_url)

        fixed_id = UUID("22222222-2222-2222-2222-222222222222")
        fixed_time = datetime(2026, 1, 1, tzinfo=UTC)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        use_case = ExampleItemUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(fixed_time),
            id_generator=FixedIdGenerator(fixed_id),
            event_handlers=[FailingExampleHandler()],
        )

        ctx = UseCaseContext(request_id="test-req-fail")
        request = ExampleItemCreateRequest(name="rollback-test")

        try:
            with pytest.raises(RuntimeError, match="故意失败"):
                await use_case.create_item(ctx, request)

            # 回滚后数据库中无数据
            count = await _count_items(database_url)
            assert count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_create_and_get_roundtrip(database_url: str) -> None:
    """创建后可按 ID 查询 — 验证 Repository add + get_by_id 闭环。"""

    from datetime import UTC, datetime
    from uuid import UUID

    await _apply_migrations(database_url)
    await _cleanup_table(database_url)
    try:
        from app.application.context import UseCaseContext
        from app.modules.example.schemas import ExampleItemCreateRequest

        engine = create_db_engine(database_url)
        fixed_id = UUID("33333333-3333-3333-3333-333333333333")
        fixed_time = datetime(2026, 6, 1, tzinfo=UTC)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        use_case = ExampleItemUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(fixed_time),
            id_generator=FixedIdGenerator(fixed_id),
            event_handlers=[],
        )

        ctx = UseCaseContext(request_id="test-roundtrip")

        try:
            # 创建
            created = await use_case.create_item(
                ctx,
                ExampleItemCreateRequest(name="roundtrip", description="desc"),
            )

            # 查询
            fetched = await use_case.get_item(ctx, created.id)
            assert fetched.name == "roundtrip"
            assert fetched.id == created.id
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_duplicate_name_raises_conflict(database_url: str) -> None:
    """重复名称触发唯一约束冲突 — SPEC 8.3 / 8.4。"""

    from datetime import UTC, datetime
    from uuid import UUID

    await _apply_migrations(database_url)
    await _cleanup_table(database_url)
    try:
        from app.application.context import UseCaseContext
        from app.modules.example.errors import ExampleItemConflictError
        from app.modules.example.schemas import ExampleItemCreateRequest

        engine = create_db_engine(database_url)
        fixed_time = datetime(2026, 1, 1, tzinfo=UTC)

        class SequentialIdGenerator(IdGenerator):
            def __init__(self) -> None:
                self._n = 0

            def generate_id(self) -> UUID:
                self._n += 1
                return UUID(int=self._n)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        use_case = ExampleItemUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(fixed_time),
            id_generator=SequentialIdGenerator(),
            event_handlers=[],
        )

        ctx = UseCaseContext(request_id="test-dup")

        try:
            # 第一次创建成功
            await use_case.create_item(
                ctx,
                ExampleItemCreateRequest(name="unique-name"),
            )

            # 第二次同名创建触发冲突
            with pytest.raises(ExampleItemConflictError):
                await use_case.create_item(
                    ctx,
                    ExampleItemCreateRequest(name="unique-name"),
                )

            # 只有一行
            count = await _count_items(database_url)
            assert count == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_list_items_pagination(database_url: str) -> None:
    """分页查询条目列表 — SPEC 9.4。"""

    from datetime import UTC, datetime

    from app.application.context import UseCaseContext

    await _apply_migrations(database_url)
    await _cleanup_table(database_url)
    try:
        engine = create_db_engine(database_url)

        # 直接插入测试数据
        async with engine.begin() as conn:
            for i in range(5):
                await conn.execute(
                    text(
                        "INSERT INTO example_items (id, name, description, "
                        "created_at, updated_at) VALUES "
                        f"(:id, :name, NULL, "
                        f"'2026-01-0{i + 1}T00:00:00Z', "
                        f"'2026-01-0{i + 1}T00:00:00Z')",
                    ),
                    {
                        "id": str(UUID(int=i + 1)),
                        "name": f"item-{i}",
                    },
                )

        class StubIdGenerator(IdGenerator):
            def generate_id(self) -> UUID:
                return uuid4()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        use_case = ExampleItemUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
            id_generator=StubIdGenerator(),
            event_handlers=[],
        )

        ctx = UseCaseContext(request_id="test-list")

        try:
            result = await use_case.list_items(
                ctx,
                page=1,
                page_size=3,
                sort_fields=[],
            )
            assert result["total"] == 5
            assert result["page"] == 1
            assert result["page_size"] == 3
            assert result["pages"] == 2
            assert len(result["items"]) == 3
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_delete_item(database_url: str) -> None:
    """删除条目 — SPEC 9.3: 无响应体删除返回 204。"""

    from datetime import UTC, datetime
    from uuid import UUID

    from app.application.context import UseCaseContext

    await _apply_migrations(database_url)
    await _cleanup_table(database_url)
    try:
        engine = create_db_engine(database_url)

        # 插入测试数据
        test_id = UUID(int=999)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO example_items (id, name, description, "
                    "created_at, updated_at) VALUES "
                    "(:id, 'delete-me', NULL, "
                    "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                ),
                {"id": str(test_id)},
            )

        class StubIdGenerator(IdGenerator):
            def generate_id(self) -> UUID:
                return uuid4()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        use_case = ExampleItemUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
            id_generator=StubIdGenerator(),
            event_handlers=[],
        )

        ctx = UseCaseContext(request_id="test-delete")

        try:
            await use_case.delete_item(ctx, test_id)
            count = await _count_items(database_url)
            assert count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)
