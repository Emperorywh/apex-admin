"""Unit of Work 集成测试（SPEC §5.6、§8.1、§8.4）。

使用 Testcontainers 启动 PostgreSQL 18 独立测试数据库（SPEC §28.2），
禁止使用 SQLite 替代。覆盖验收条件：
- 事务提交：UoW 无异常退出时数据持久化
- 事务回滚：UoW 异常退出时数据不持久化
- 异常映射：数据库完整性错误映射为 IntegrityConstraintError
- 并发会话隔离：asyncio.gather 并发任务使用各自独立的 AsyncSession
- 连接池在 lifespan 中初始化和释放

前置条件：运行环境需要 Docker（Testcontainers 依赖）。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from app.config.settings import AppEnv, Settings
from app.errors import IntegrityConstraintError
from app.health.providers import DbPoolProvider
from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider
from app.infrastructure.database.engine import create_engine
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = [pytest.mark.integration, pytest.mark.g1]

# 测试用有效密钥（64 位 hex = 32 字节，字节值多样，非退化密钥）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"

# 测试表 DDL：name 列有 UNIQUE 约束，用于测试完整性约束冲突
_TEST_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS test_uow_items (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
)
"""


@pytest.fixture(scope="module")
def pg_container() -> PostgresContainer:  # type: ignore[misc]
    """启动 PostgreSQL 18 容器，模块级共享以减少容器启停开销。

    使用 ``driver='psycopg'`` 确保 get_connection_url() 返回
    ``postgresql+psycopg://`` 格式的 URL（SPEC §5.4）。
    """
    container = PostgresContainer("postgres:18", driver="psycopg")
    container.start()
    yield container  # type: ignore[misc]
    container.stop()


def _make_settings(database_url: str) -> Settings:
    """构造连接到测试容器的 Settings。"""
    return Settings(
        _env_file=None,
        app_env=AppEnv.TESTING,
        database_url=database_url,
        access_token_hmac_key=_VALID_ACCESS_KEY,
        refresh_token_hmac_key=_VALID_REFRESH_KEY,
        config_encryption_key=_VALID_ENCRYPTION_KEY,
        file_storage_root="/tmp/apex-test-files",
    )


@pytest.fixture
async def engine(pg_container: PostgresContainer) -> AsyncEngine:
    """创建连接到测试容器的 AsyncEngine 并准备测试表。

    每个测试函数获得干净的表状态。
    """
    url = pg_container.get_connection_url()
    eng = create_engine(url, pool_size=3, max_overflow=2)

    async with eng.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS test_uow_items"))
        await conn.execute(text(_TEST_TABLE_DDL))

    yield eng

    await eng.dispose()


# ---------------------------------------------------------------------------
# 事务提交（验收条件：SqlAlchemyUnitOfWork 创建 AsyncSession，退出时提交）
# ---------------------------------------------------------------------------


class TestTransactionCommit:
    """验证 UoW 无异常退出时事务正确提交。"""

    async def test_commit_persists_data(self, engine: AsyncEngine) -> None:
        """UoW 正常退出后插入的数据持久化到数据库。"""
        async with SqlAlchemyUnitOfWork(engine) as uow:
            await uow.session.execute(
                text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                {"name": "alpha"},
            )

        # UoW 退出后使用独立连接验证数据已持久化
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT name FROM test_uow_items"))
            rows = result.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "alpha"

    async def test_session_closed_after_exit(self, engine: AsyncEngine) -> None:
        """UoW 退出后会话已关闭，再次访问 session 属性抛出 RuntimeError。"""
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            _ = uow.session  # 上下文内可访问

        with pytest.raises(RuntimeError, match="未激活"):
            _ = uow.session

    async def test_explicit_commit_then_exit(self, engine: AsyncEngine) -> None:
        """显式调用 commit 后正常退出，数据持久化。"""
        async with SqlAlchemyUnitOfWork(engine) as uow:
            await uow.session.execute(
                text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                {"name": "beta"},
            )
            await uow.commit()

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM test_uow_items"))
            assert result.scalar() == 1


# ---------------------------------------------------------------------------
# 事务回滚（验收条件：UoW 异常退出时事务回滚）
# ---------------------------------------------------------------------------


class TestTransactionRollback:
    """验证 UoW 异常退出时事务回滚，数据不持久化。"""

    async def test_rollback_on_exception(self, engine: AsyncEngine) -> None:
        """UoW 内抛出异常时插入的数据被回滚。"""
        with pytest.raises(ValueError, match="业务异常"):
            async with SqlAlchemyUnitOfWork(engine) as uow:
                await uow.session.execute(
                    text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                    {"name": "gamma"},
                )
                raise ValueError("业务异常")

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM test_uow_items"))
            assert result.scalar() == 0

    async def test_explicit_rollback(self, engine: AsyncEngine) -> None:
        """显式调用 rollback 后已暂存的变更被丢弃。"""
        async with SqlAlchemyUnitOfWork(engine) as uow:
            await uow.session.execute(
                text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                {"name": "delta"},
            )
            await uow.rollback()
            # rollback 后 UoW 退出时正常提交（无新变更）

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM test_uow_items"))
            assert result.scalar() == 0


# ---------------------------------------------------------------------------
# 异常映射（验收条件：数据库异常映射为稳定应用异常）
# ---------------------------------------------------------------------------


class TestExceptionMapping:
    """验证数据库完整性错误映射为 IntegrityConstraintError。"""

    async def test_unique_constraint_violation_during_commit(self, engine: AsyncEngine) -> None:
        """唯一约束冲突在提交时映射为 IntegrityConstraintError。"""
        # 先插入一条记录
        async with SqlAlchemyUnitOfWork(engine) as uow:
            await uow.session.execute(
                text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                {"name": "unique_name"},
            )

        # 插入重复值，提交时触发唯一约束冲突
        with pytest.raises(IntegrityConstraintError) as exc_info:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                await uow.session.execute(
                    text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                    {"name": "unique_name"},
                )

        # 验证错误码稳定
        assert exc_info.value.code == "DB.INTEGRITY_CONSTRAINT"
        # 原始异常作为 cause 链接
        assert exc_info.value.__cause__ is not None

    async def test_constraint_error_does_not_leak_sql(self, engine: AsyncEngine) -> None:
        """映射后的异常消息不包含 SQL 语句或约束名等技术细节。"""
        async with SqlAlchemyUnitOfWork(engine):
            pass  # 确保表存在

        # 先插入一条
        async with SqlAlchemyUnitOfWork(engine) as uow:
            await uow.session.execute(
                text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                {"name": "leak_test"},
            )

        with pytest.raises(IntegrityConstraintError) as exc_info:
            async with SqlAlchemyUnitOfWork(engine) as uow:
                await uow.session.execute(
                    text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                    {"name": "leak_test"},
                )

        # 异常消息和错误码不包含底层 SQL 或约束名
        message = str(exc_info.value)
        assert "test_uow_items" not in message
        assert "INSERT" not in message.upper()


# ---------------------------------------------------------------------------
# 并发会话隔离（验收条件：禁止在 asyncio.gather 并发任务间共享 AsyncSession）
# ---------------------------------------------------------------------------


class TestConcurrentSessionIsolation:
    """验证并发任务使用各自独立的 AsyncSession。"""

    async def test_each_uow_has_independent_session(self, engine: AsyncEngine) -> None:
        """两个 UoW 实例创建各自独立的 AsyncSession。"""
        uow1 = SqlAlchemyUnitOfWork(engine)
        uow2 = SqlAlchemyUnitOfWork(engine)

        async with uow1, uow2:
            assert uow1.session is not uow2.session

    async def test_concurrent_uows_succeed_independently(self, engine: AsyncEngine) -> None:
        """asyncio.gather 中各自独立的 UoW 并发执行成功。

        并发任务必须分别创建各自的 UoW，不得共享同一个 AsyncSession
        （SPEC §5.6）。
        """

        async def insert_item(eng: AsyncEngine, name: str) -> None:
            """单个并发任务：创建独立 UoW 并插入一条记录。"""
            async with SqlAlchemyUnitOfWork(eng) as uow:
                await uow.session.execute(
                    text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                    {"name": name},
                )

        # 并发执行 3 个任务，每个使用独立 UoW
        await asyncio.gather(
            insert_item(engine, "concurrent_1"),
            insert_item(engine, "concurrent_2"),
            insert_item(engine, "concurrent_3"),
        )

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT name FROM test_uow_items ORDER BY name"))
            names = [row[0] for row in result.fetchall()]
        assert names == ["concurrent_1", "concurrent_2", "concurrent_3"]

    async def test_concurrent_with_conflict_one_succeeds(self, engine: AsyncEngine) -> None:
        """并发插入重复值时，只有一个成功，另一个映射为 IntegrityConstraintError。"""

        async def try_insert(eng: AsyncEngine, name: str) -> str:
            try:
                async with SqlAlchemyUnitOfWork(eng) as uow:
                    await uow.session.execute(
                        text("INSERT INTO test_uow_items (name) VALUES (:name)"),
                        {"name": name},
                    )
                return "ok"
            except IntegrityConstraintError:
                return "conflict"

        outcomes = await asyncio.gather(
            try_insert(engine, "same_name"),
            try_insert(engine, "same_name"),
        )

        assert "ok" in outcomes
        assert "conflict" in outcomes


# ---------------------------------------------------------------------------
# 连接池生命周期（验收条件：连接池在 lifespan 中初始化并释放）
# ---------------------------------------------------------------------------


class TestDbPoolProviderLifecycle:
    """验证 SqlAlchemyDbPoolProvider 的初始化、连通性检查和释放。"""

    async def test_initialize_and_check(self, pg_container: PostgresContainer) -> None:
        """provider 初始化后连通性检查返回 True。"""
        settings = _make_settings(pg_container.get_connection_url())
        provider = SqlAlchemyDbPoolProvider(settings)

        await provider.initialize()
        assert await provider.check_connection() is True
        await provider.dispose()

    async def test_dispose_releases_pool(self, pg_container: PostgresContainer) -> None:
        """provider dispose 后连通性检查返回 False。"""
        settings = _make_settings(pg_container.get_connection_url())
        provider = SqlAlchemyDbPoolProvider(settings)

        await provider.initialize()
        assert await provider.check_connection() is True

        await provider.dispose()
        assert await provider.check_connection() is False

    async def test_create_uow_after_init(self, pg_container: PostgresContainer) -> None:
        """provider 初始化后可创建 UoW 并执行数据库操作。"""
        settings = _make_settings(pg_container.get_connection_url())
        provider = SqlAlchemyDbPoolProvider(settings)

        await provider.initialize()
        uow = provider.create_unit_of_work()
        assert isinstance(uow, SqlAlchemyUnitOfWork)

        async with uow:
            result = await uow.session.execute(text("SELECT 1"))
            assert result.scalar() == 1

        await provider.dispose()

    async def test_provider_is_db_pool_provider(self, pg_container: PostgresContainer) -> None:
        """SqlAlchemyDbPoolProvider 是 DbPoolProvider 的子类。"""
        settings = _make_settings(pg_container.get_connection_url())
        provider = SqlAlchemyDbPoolProvider(settings)
        assert isinstance(provider, DbPoolProvider)


# ---------------------------------------------------------------------------
# 连接池配置（验收条件：可配置 pool_size 和 max_overflow）
# ---------------------------------------------------------------------------


class TestEnginePoolConfig:
    """验证 create_async_engine 使用 postgresql+psycopg 并支持连接池配置。"""

    async def test_engine_uses_psycopg_url(self, pg_container: PostgresContainer) -> None:
        """引擎连接 URL 使用 postgresql+psycopg 协议。"""
        url = pg_container.get_connection_url()
        assert url.startswith("postgresql+psycopg://")

        eng = create_engine(url, pool_size=2, max_overflow=1)
        async with eng.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        await eng.dispose()

    async def test_engine_accepts_custom_pool_params(self, pg_container: PostgresContainer) -> None:
        """引擎接受自定义 pool_size 和 max_overflow 参数。"""
        url = pg_container.get_connection_url()
        eng = create_engine(url, pool_size=10, max_overflow=20)

        async with eng.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

        await eng.dispose()
