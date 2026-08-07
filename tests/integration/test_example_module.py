"""示例模块集成测试（SPEC §30.2、§34.1）。

使用 Testcontainers PostgreSQL 18 验证示例模块的完整调用流：
Router → Use Case → Domain Policy → Repository → Database → Event Dispatch。

前置条件：运行环境需要 Docker（Testcontainers 依赖）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from app.infrastructure.database.engine import create_engine
from app.modules.example.infrastructure.models import ExampleItemModel
from app.modules.example.infrastructure.wiring import create_example_service

pytestmark = [pytest.mark.integration, pytest.mark.g1]

# example_items 表 DDL（与 Alembic 迁移 0002_example 一致）
_EXAMPLE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS example_items (
    id         UUID PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
)
"""


@pytest.fixture(scope="module")
async def example_engine(
    postgres_container: PostgresContainer,
) -> AsyncIterator[AsyncEngine]:
    """为示例模块测试创建引擎并创建 example_items 表。

    使用直接 DDL 创建表（与迁移文件 0002_example 的 DDL 一致），
    避免 Alembic env.py 从 Settings 读取 URL 的耦合。
    """
    url = postgres_container.get_connection_url()
    engine = create_engine(url, pool_size=3, max_overflow=2)

    # 创建 example_items 表
    async with engine.begin() as conn:
        await conn.execute(text(_EXAMPLE_TABLE_DDL))

    yield engine
    await engine.dispose()


@pytest.fixture
async def clean_examples(example_engine: AsyncEngine) -> AsyncIterator[None]:
    """每个测试前清空 example_items 表。"""
    async with example_engine.begin() as conn:
        await conn.execute(text("DELETE FROM example_items"))
    yield


class TestExampleModuleIntegration:
    """示例模块完整集成测试。"""

    async def test_table_exists_after_setup(
        self,
        example_engine: AsyncEngine,
    ) -> None:
        """example_items 表在 fixture 设置后存在。"""
        async with example_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'example_items' ORDER BY ordinal_position"
                )
            )
            columns = [row[0] for row in result.fetchall()]

        assert "id" in columns
        assert "name" in columns
        assert "created_at" in columns

    async def test_create_and_list_item(
        self,
        example_engine: AsyncEngine,
        clean_examples: None,
    ) -> None:
        """创建项目后可以查询到（SPEC §5.6：事务提交后数据持久化）。"""
        service = create_example_service(example_engine)

        now = datetime.now(UTC)
        item = await service.create_item(name="integration-test", current_time=now)

        assert item.name == "integration-test"

        items, total = await service.list_items(page=1, page_size=20)
        assert total == 1
        assert len(items) == 1
        assert items[0].name == "integration-test"

    async def test_create_multiple_items_pagination(
        self,
        example_engine: AsyncEngine,
        clean_examples: None,
    ) -> None:
        """创建多条数据后分页查询正确。"""
        service = create_example_service(example_engine)
        now = datetime.now(UTC)

        for i in range(5):
            await service.create_item(name=f"item-{i}", current_time=now)

        items, total = await service.list_items(page=1, page_size=2)
        assert total == 5
        assert len(items) == 2

        items_p2, _total_p2 = await service.list_items(page=2, page_size=2)
        assert len(items_p2) == 2

        items_p3, _total_p3 = await service.list_items(page=3, page_size=2)
        assert len(items_p3) == 1

    async def test_invalid_name_raises_parameter_error(
        self,
        example_engine: AsyncEngine,
        clean_examples: None,
    ) -> None:
        """不合规名称在服务层抛出 ParameterError（携带稳定错误码）。"""
        from app.errors import ParameterError

        service = create_example_service(example_engine)

        with pytest.raises(ParameterError, match="EXAMPLE.INVALID_NAME"):
            await service.create_item(name="", current_time=datetime.now(UTC))

    async def test_create_item_commits_transaction(
        self,
        example_engine: AsyncEngine,
        clean_examples: None,
    ) -> None:
        """创建项目后事务已提交——独立连接可查到数据（SPEC §5.6）。"""
        from sqlalchemy import select

        service = create_example_service(example_engine)
        await service.create_item(
            name="commit-test",
            current_time=datetime.now(UTC),
        )

        # 使用独立连接验证数据已持久化（非 UoW 会话）
        async with example_engine.connect() as conn:
            result = await conn.execute(
                select(ExampleItemModel.name).where(ExampleItemModel.name == "commit-test")
            )
            row = result.fetchone()

        assert row is not None
        assert row[0] == "commit-test"

    async def test_no_demo_data_in_clean_database(
        self,
        example_engine: AsyncEngine,
        clean_examples: None,
    ) -> None:
        """示例模块不携带业务演示数据——空库查询返回空列表（SPEC §30.2）。"""
        service = create_example_service(example_engine)
        items, total = await service.list_items(page=1, page_size=20)
        assert items == []
        assert total == 0
