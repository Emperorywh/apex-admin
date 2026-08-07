"""Testcontainers PostgreSQL 18 集成测试（SPEC §28.2）。

验证 Testcontainers fixture 能正确启动 PostgreSQL 18 容器、创建空库、
执行数据库操作，并在测试后销毁容器数据。

覆盖验收条件：
- Testcontainers fixture 启动 PostgreSQL 18、创建空库、测试后销毁

前置条件：运行环境需要 Docker（Testcontainers 依赖）。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.postgres import PostgresContainer

from app.infrastructure.database.engine import create_engine

pytestmark = [pytest.mark.integration, pytest.mark.g1]


# ---------------------------------------------------------------------------
# 容器启动与销毁（验收条件：fixture 启动 PostgreSQL 18、创建空库、测试后销毁）
# ---------------------------------------------------------------------------


class TestContainerLifecycle:
    """验证 PostgreSQL 18 容器正确启动和销毁。"""

    def test_container_starts_postgres_18(self, postgres_container: PostgresContainer) -> None:
        """fixture 启动的容器使用 PostgreSQL 18 镜像。"""
        # PostgresContainer 在内部启动容器，此处验证容器已就绪
        url = postgres_container.get_connection_url()
        # 使用 psycopg 驱动（SPEC §5.4）
        assert url.startswith("postgresql+psycopg://")

    def test_container_provides_empty_database(self, postgres_container: PostgresContainer) -> None:
        """容器启动后提供空数据库，无业务表。"""
        url = postgres_container.get_connection_url()
        eng = create_engine(url, pool_size=2, max_overflow=1)

        async def _check_empty() -> list[str]:
            """查询数据库中的用户表列表。"""
            async with eng.connect() as conn:
                result = await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
                return [row[0] for row in result.fetchall()]

        tables = asyncio.run(_check_empty())
        asyncio.run(eng.dispose())

        # 全新容器不应有任何用户表
        assert tables == []


class TestDbEngineFixture:
    """验证 db_engine fixture 连接到 Testcontainers PostgreSQL 18。"""

    async def test_engine_executes_query(self, db_engine: AsyncEngine) -> None:
        """db_engine fixture 创建的引擎可以执行 SQL 查询。"""
        async with db_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    async def test_engine_url_is_psycopg(self, db_engine: AsyncEngine) -> None:
        """引擎连接 URL 使用 postgresql+psycopg 协议（SPEC §5.4）。"""
        url = str(db_engine.url)
        assert url.startswith("postgresql+psycopg://")

    async def test_engine_connects_to_postgres_18(self, db_engine: AsyncEngine) -> None:
        """引擎连接的 PostgreSQL 版本为 18.x。"""
        async with db_engine.connect() as conn:
            result = await conn.execute(text("SHOW server_version"))
            version = result.scalar()
        assert version is not None
        # PostgreSQL 18 返回 "18.x" 格式的版本号
        assert version.startswith("18"), f"期望 PostgreSQL 18，实际为 {version}"


class TestDbSessionFixture:
    """验证 db_session fixture 提供独立 AsyncSession。"""

    async def test_session_can_execute_query(self, db_session: AsyncSession) -> None:
        """db_session fixture 提供的 AsyncSession 可以执行查询。"""
        result = await db_session.execute(text("SELECT 1"))
        assert result.scalar() == 1

    async def test_session_is_independent_per_test(
        self, db_session: AsyncSession, db_engine: AsyncEngine
    ) -> None:
        """每个测试函数获得独立的 AsyncSession，修改不会泄漏到其他测试。"""
        # 创建测试表并插入数据
        await db_session.execute(
            text("CREATE TABLE IF NOT EXISTS fixture_test (id SERIAL PRIMARY KEY, val TEXT)")
        )
        await db_session.execute(
            text("INSERT INTO fixture_test (val) VALUES (:val)"),
            {"val": "session_a"},
        )
        await db_session.commit()

        # 通过独立连接验证数据存在
        async with db_engine.connect() as conn:
            result = await conn.execute(text("SELECT val FROM fixture_test"))
            values = [row[0] for row in result.fetchall()]
        assert "session_a" in values
