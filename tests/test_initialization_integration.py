"""幂等初始化框架集成测试 — SPEC 8.5.

覆盖验收标准:
  - AC-3: 初始化器以稳定自然键幂等 upsert，
    重复执行不产生重复数据（集成测试）。

SPEC 8.5 关键约束:
  - 初始化器使用稳定自然键或稳定编码执行幂等 upsert，
    不得按显示名称判断重复。
  - 初始化过程可重复执行且不会创建重复数据。
  - 初始化器只能写入本模块拥有的数据。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from app.core.initialization.framework import InitializationRunner, Initializer
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── 测试表 ───────────────────────────────────────────────────────────────

_TEST_TABLE = "test_seed_data"


async def _setup_table(database_url: str) -> None:
    """创建种子数据测试表（含自然键唯一约束）。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TEST_TABLE}"))
            await conn.execute(
                text(
                    f"CREATE TABLE {_TEST_TABLE} ("
                    f"  id serial PRIMARY KEY,"
                    f"  natural_key text NOT NULL UNIQUE,"
                    f"  display_name text NOT NULL,"
                    f"  value text"
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
    """查询行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(f"SELECT count(*) FROM {_TEST_TABLE}"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _get_value(database_url: str, natural_key: str) -> str | None:
    """查询指定自然键的 value。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    f"SELECT value FROM {_TEST_TABLE} WHERE natural_key = :key",
                ),
                {"key": natural_key},
            )
            row = result.first()
            return row[0] if row else None
    finally:
        await engine.dispose()


# ── 测试用初始化器 ───────────────────────────────────────────────────────


class SeedInitializer(Initializer):
    """测试用初始化器 — 使用稳定自然键幂等 upsert。

    SPEC 8.5: "初始化器使用稳定自然键或稳定编码执行幂等 upsert，
    不得按显示名称判断重复"。

    使用 ``ON CONFLICT (natural_key) DO UPDATE`` 实现幂等 upsert，
    以自然键（非显示名称）判断重复。
    """

    def __init__(
        self,
        code: str,
        natural_key: str,
        display_name: str,
        value: str,
    ) -> None:
        self._code = code
        self._natural_key = natural_key
        self._display_name = display_name
        self._value = value

    @property
    def code(self) -> str:
        return self._code

    async def initialize(self, session: AsyncSession) -> None:
        """使用 ON CONFLICT 执行幂等 upsert.

        SPEC 8.5: "不得按显示名称判断重复"。
        以 natural_key 作为唯一判断依据。
        """

        await session.execute(
            text(
                f"INSERT INTO {_TEST_TABLE} "
                f"(natural_key, display_name, value) "
                f"VALUES (:natural_key, :display_name, :value) "
                f"ON CONFLICT (natural_key) DO UPDATE SET "
                f"  display_name = EXCLUDED.display_name, "
                f"  value = EXCLUDED.value",
            ),
            {
                "natural_key": self._natural_key,
                "display_name": self._display_name,
                "value": self._value,
            },
        )


# ── 集成测试 ─────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.integration
async def test_initializer_idempotent_single_run(database_url: str) -> None:
    """初始化器单次执行写入数据（AC-3）。"""

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)
        initializer = SeedInitializer(
            code="TEST.SEED_BASIC",
            natural_key="role:admin",
            display_name="管理员",
            value="initial",
        )
        runner = InitializationRunner([initializer])

        uow = SqlAlchemyUnitOfWork(engine)
        try:
            async with uow:
                await runner.run(uow.session)
                await uow.commit()

            count = await _count_rows(database_url)
            assert count == 1

            value = await _get_value(database_url, "role:admin")
            assert value == "initial"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_initializer_idempotent_repeated_run(database_url: str) -> None:
    """初始化器重复执行不产生重复数据（AC-3）。

    SPEC 8.5: "初始化过程可重复执行且不会创建重复数据"。
    """

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)
        initializer = SeedInitializer(
            code="TEST.SEED_BASIC",
            natural_key="role:admin",
            display_name="管理员",
            value="initial",
        )

        # 第一次执行
        runner = InitializationRunner([initializer])
        uow1 = SqlAlchemyUnitOfWork(engine)
        async with uow1:
            await runner.run(uow1.session)
            await uow1.commit()

        count_after_first = await _count_rows(database_url)
        assert count_after_first == 1

        # 第二次执行（相同数据）
        uow2 = SqlAlchemyUnitOfWork(engine)
        async with uow2:
            await runner.run(uow2.session)
            await uow2.commit()

        count_after_second = await _count_rows(database_url)
        assert count_after_second == 1  # 不产生重复

        # 第三次执行
        uow3 = SqlAlchemyUnitOfWork(engine)
        async with uow3:
            await runner.run(uow3.session)
            await uow3.commit()

        count_after_third = await _count_rows(database_url)
        assert count_after_third == 1

        await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_initializer_updates_value_on_rerun(database_url: str) -> None:
    """初始化器重复执行时更新而非插入（幂等 upsert）。"""

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)

        # 第一次插入
        init_v1 = SeedInitializer(
            code="TEST.SEED_BASIC",
            natural_key="config:base",
            display_name="基础配置",
            value="v1",
        )
        runner1 = InitializationRunner([init_v1])
        uow1 = SqlAlchemyUnitOfWork(engine)
        async with uow1:
            await runner1.run(uow1.session)
            await uow1.commit()

        # 第二次用不同 value 执行（相同自然键）
        init_v2 = SeedInitializer(
            code="TEST.SEED_BASIC",
            natural_key="config:base",
            display_name="基础配置",
            value="v2",
        )
        runner2 = InitializationRunner([init_v2])
        uow2 = SqlAlchemyUnitOfWork(engine)
        async with uow2:
            await runner2.run(uow2.session)
            await uow2.commit()

        count = await _count_rows(database_url)
        assert count == 1  # 仍然只有一行

        value = await _get_value(database_url, "config:base")
        assert value == "v2"  # value 被更新

        await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_multiple_initializers_run_in_sequence(database_url: str) -> None:
    """多个初始化器按编码稳定排序顺序执行。"""

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)
        initializers = [
            SeedInitializer(
                code="TEST.SEED_ZULU",
                natural_key="seed:zulu",
                display_name="Zulu",
                value="z",
            ),
            SeedInitializer(
                code="TEST.SEED_ALPHA",
                natural_key="seed:alpha",
                display_name="Alpha",
                value="a",
            ),
        ]
        runner = InitializationRunner(initializers)

        # 验证稳定排序
        codes = [i.code for i in runner.initializers]
        assert codes == ["TEST.SEED_ALPHA", "TEST.SEED_ZULU"]

        uow = SqlAlchemyUnitOfWork(engine)
        try:
            async with uow:
                await runner.run(uow.session)
                await uow.commit()

            count = await _count_rows(database_url)
            assert count == 2
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_initializer_failure_rolls_back(database_url: str) -> None:
    """初始化器失败时整体回滚。"""

    await _setup_table(database_url)
    try:
        engine = create_db_engine(database_url)

        class FailingInitializer(Initializer):
            @property
            def code(self) -> str:
                return "TEST.FAILING"

            async def initialize(self, session: AsyncSession) -> None:
                raise RuntimeError("初始化器故意失败")

        runner = InitializationRunner(
            [
                SeedInitializer(
                    code="TEST.SEED_OK",
                    natural_key="seed:ok",
                    display_name="OK",
                    value="ok",
                ),
                FailingInitializer(),
            ],
        )

        uow = SqlAlchemyUnitOfWork(engine)
        try:
            with pytest.raises(RuntimeError, match="初始化器故意失败"):
                async with uow:
                    await runner.run(uow.session)
                    await uow.commit()

            # 回滚后无数据
            count = await _count_rows(database_url)
            assert count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_table(database_url)
