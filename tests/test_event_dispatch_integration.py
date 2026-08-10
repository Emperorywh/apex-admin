"""事务内事件分发集成测试 — SPEC 5.7.

覆盖验收标准:
  - AC-2: 事务内事件处理器在 UoW 提交前同步执行，
    任一处理器失败整个 Use Case 回滚（真库集成测试），
    处理器按稳定顺序执行。
  - VERIFY-004: 事件机制不含隐式副作用与顺序依赖。

SPEC 5.7 关键约束:
  - 事务内事件处理器在当前 Unit of Work 提交前同步执行。
  - 任一事务内处理器失败时，整个 Use Case 回滚。
  - 多处理器不得依赖执行顺序；稳定排序只用于保证测试和日志可复现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from app.core.events.dispatcher import TransactionalEventDispatcher
from app.core.events.events import DomainEvent, validate_event_code
from app.core.events.handlers import TransactionalEventHandler
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── 测试表与辅助 ───────────────────────────────────────────────────────────

_TEST_TABLE = "test_event_log"


async def _setup_table(database_url: str) -> None:
    """创建事件日志测试表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TEST_TABLE}"))
            await conn.execute(
                text(
                    f"CREATE TABLE {_TEST_TABLE} ("
                    f"  id serial PRIMARY KEY,"
                    f"  source text NOT NULL,"
                    f"  event_code text NOT NULL,"
                    f"  payload_key text"
                    f")",
                ),
            )
    finally:
        await engine.dispose()


async def _cleanup_table(database_url: str) -> None:
    """清理测试表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TEST_TABLE}"))
    finally:
        await engine.dispose()


async def _count_rows(database_url: str) -> int:
    """查询日志表行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(f"SELECT count(*) FROM {_TEST_TABLE}"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


# ── 测试用事件处理器 ─────────────────────────────────────────────────────


class LogEventHandler(TransactionalEventHandler):
    """测试用事件处理器 — 在事件日志表中插入记录。"""

    def __init__(
        self,
        handler_code: str,
        event_code: str,
        source: str,
    ) -> None:
        self._code = handler_code
        self._event_code = event_code
        self._source = source

    @property
    def code(self) -> str:
        return self._code

    @property
    def event_code(self) -> str:
        return self._event_code

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        await session.execute(
            text(
                f"INSERT INTO {_TEST_TABLE} (source, event_code, payload_key) "
                f"VALUES (:source, :event_code, :payload_key)",
            ),
            {
                "source": self._source,
                "event_code": event.code,
                "payload_key": event.payload.get("key"),
            },
        )


class FailingEventHandler(TransactionalEventHandler):
    """测试用失败处理器 — 抛出异常触发回滚。"""

    def __init__(self, handler_code: str, event_code: str) -> None:
        self._code = handler_code
        self._event_code = event_code

    @property
    def code(self) -> str:
        return self._code

    @property
    def event_code(self) -> str:
        return self._event_code

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        raise RuntimeError(f"处理器 {self._code} 故意失败")


# ── 事件分发集成测试 ──────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.integration
async def test_event_handler_executes_before_commit(database_url: str) -> None:
    """事务内事件处理器在 UoW 提交前同步执行（AC-2）。

    验证: 处理器在 commit 前执行，commit 后数据和处理器写入同时持久化。
    """

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)
        handler = LogEventHandler("TEST.LOG_HANDLER", "TEST.CREATED", "handler-a")
        dispatcher = TransactionalEventDispatcher([handler])

        event = DomainEvent(code="TEST.CREATED", payload={"key": "value"})

        uow = SqlAlchemyUnitOfWork(engine)
        try:
            async with uow:
                # 先写入业务数据
                await uow.session.execute(
                    text(
                        f"INSERT INTO {_TEST_TABLE} (source, event_code, payload_key) "
                        f"VALUES ('business', 'BUSINESS', 'biz')",
                    ),
                )
                # 收集事件
                dispatcher.collect(event)
                # 在 commit 前分发
                await dispatcher.dispatch(uow.session)
                await uow.commit()

            # commit 后: 业务数据 + 处理器写入均持久化
            count = await _count_rows(database_url)
            assert count == 2
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_handler_failure_causes_rollback(database_url: str) -> None:
    """任一处理器失败时整个 Use Case 回滚（AC-2）。

    验证: 一个处理器成功写入，另一个处理器失败，
    整个事务回滚，所有写入（包括成功的处理器写入）都不持久化。
    """

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)
        success_handler = LogEventHandler(
            "TEST.SUCCESS_HANDLER",
            "TEST.CREATED",
            "success",
        )
        fail_handler = FailingEventHandler("TEST.FAIL_HANDLER", "TEST.CREATED")
        dispatcher = TransactionalEventDispatcher([success_handler, fail_handler])

        event = DomainEvent(code="TEST.CREATED")

        uow = SqlAlchemyUnitOfWork(engine)
        try:
            with pytest.raises(RuntimeError, match="故意失败"):
                async with uow:
                    # 写入业务数据
                    await uow.session.execute(
                        text(
                            f"INSERT INTO {_TEST_TABLE} (source, event_code) "
                            f"VALUES ('business', 'BUSINESS')",
                        ),
                    )
                    dispatcher.collect(event)
                    # dispatch 时 fail_handler 会抛异常
                    await dispatcher.dispatch(uow.session)
                    await uow.commit()

            # 回滚后: 无任何数据持久化
            count = await _count_rows(database_url)
            assert count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_handlers_execute_in_stable_order(database_url: str) -> None:
    """处理器按稳定排序执行（AC-2）。

    SPEC 5.7: "多处理器不得依赖执行顺序；稳定排序只用于保证测试和日志可复现"。
    验证: 处理器按 code 字典序执行，多次运行结果一致。
    """

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)

        # 故意以非字典序构造
        handlers = [
            LogEventHandler("TEST.ZULU", "TEST.CREATED", "zulu"),
            LogEventHandler("TEST.ALPHA", "TEST.CREATED", "alpha"),
            LogEventHandler("TEST.MID", "TEST.CREATED", "mid"),
        ]
        dispatcher = TransactionalEventDispatcher(handlers)

        event = DomainEvent(code="TEST.CREATED")

        uow = SqlAlchemyUnitOfWork(engine)
        try:
            async with uow:
                dispatcher.collect(event)
                await dispatcher.dispatch(uow.session)
                await uow.commit()

            # 验证按 handler code 字典序写入
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        f"SELECT source FROM {_TEST_TABLE} ORDER BY id",
                    ),
                )
                sources = [row[0] for row in result]

            assert sources == ["alpha", "mid", "zulu"]
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_no_handlers_dispatch_is_noop(database_url: str) -> None:
    """无匹配处理器时分发为空操作，不影响事务。"""

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)
        dispatcher = TransactionalEventDispatcher([])

        event = DomainEvent(code="ORPHAN.EVENT")

        uow = SqlAlchemyUnitOfWork(engine)
        try:
            async with uow:
                await uow.session.execute(
                    text(
                        f"INSERT INTO {_TEST_TABLE} (source, event_code) "
                        f"VALUES ('biz', 'BIZ')",
                    ),
                )
                dispatcher.collect(event)
                await dispatcher.dispatch(uow.session)
                await uow.commit()

            count = await _count_rows(database_url)
            assert count == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.unit
def test_event_code_format_validation() -> None:
    """事件编码格式校验 — SPEC 5.7。"""

    # 合法格式
    validate_event_code("USER.CREATED")
    validate_event_code("AUTH.LOGIN_SUCCEEDED")

    # 非法格式
    with pytest.raises(ValueError, match="事件编码格式非法"):
        validate_event_code("invalid")
    with pytest.raises(ValueError, match="事件编码格式非法"):
        validate_event_code("user.created")
    with pytest.raises(ValueError, match="事件编码格式非法"):
        validate_event_code("USER.")


@pytest.mark.g1
@pytest.mark.unit
def test_dispatcher_stable_sort() -> None:
    """分发器按 (code, event_code) 稳定排序处理器。"""

    h1 = LogEventHandler("TEST.ZULU", "TEST.A", "z")
    h2 = LogEventHandler("TEST.ALPHA", "TEST.B", "a")
    h3 = LogEventHandler("TEST.MID", "TEST.A", "m")

    dispatcher = TransactionalEventDispatcher([h1, h2, h3])
    codes = [h.code for h in dispatcher.handlers]

    assert codes == ["TEST.ALPHA", "TEST.MID", "TEST.ZULU"]
