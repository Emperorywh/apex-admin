"""测试根配置（SPEC §28）。

提供可复用的 pytest fixture，供所有测试模块共享，避免重复定义
测试配置、容器管理、应用创建和客户端构造代码。

提供的 fixture：
- ``app``：创建配置好的 FastAPI 应用实例（单元测试级别，不连接真实数据库）
- ``client``：基于 ``app`` 的 :class:`~fastapi.testclient.TestClient`
- ``db_engine``：连接到 Testcontainers PostgreSQL 18 的 AsyncEngine（集成测试级别）
- ``db_session``：从 ``db_engine`` 创建的独立 AsyncSession（集成测试级别）

fixture 分层说明：
- ``app`` 和 ``client`` 不依赖 Docker，适用于单元和 API 契约测试
- ``db_engine`` 和 ``db_session`` 依赖 Docker（Testcontainers），仅用于集成测试，
  使用 ``integration`` marker 标记的测试方可安全引用
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.app import create_app
from app.config.settings import AppEnv, Settings
from app.health.providers import DbPoolProvider, ReadinessProbe

# 注册 Testcontainers fixture 模块（SPEC §28.2）
# pytest_plugins 使 tests/fixtures/database.py 中定义的会话级容器 fixture
# 可被所有测试模块通过参数注入使用
pytest_plugins = ("tests.fixtures.database",)

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer

# 测试用有效密钥（64 位 hex = 32 字节，字节值多样，非退化密钥）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"


class FakeDbPoolProvider(DbPoolProvider):
    """测试用的假数据库连接池 provider。

    通过 ``connected`` 属性控制数据库连通性，
    用于测试就绪检查的 200/503 行为，不连接真实数据库。
    """

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.initialized = False
        self.disposed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def dispose(self) -> None:
        self.disposed = True

    async def check_connection(self) -> bool:
        return self.connected


class FakeRevisionProbe(ReadinessProbe):
    """测试用的假 Alembic revision 探针。"""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def probe(self) -> bool:
        return self.ready


def make_unit_test_settings() -> Settings:
    """构造单元测试用 Settings。

    禁用 .env 加载，使用 testing 环境。数据库 URL 指向本地占位地址
    （单元测试不连接真实数据库）。
    """
    return Settings(
        _env_file=None,
        app_env=AppEnv.TESTING,
        database_url="postgresql+psycopg://apex:secret@localhost:5432/apex_admin_test",
        access_token_hmac_key=_VALID_ACCESS_KEY,
        refresh_token_hmac_key=_VALID_REFRESH_KEY,
        config_encryption_key=_VALID_ENCRYPTION_KEY,
        file_storage_root="/tmp/apex-test-files",
    )


# ---------------------------------------------------------------------------
# 可复用 fixture：app（SPEC §28、验收条件 5）
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """创建配置好的 FastAPI 应用实例。

    使用 testing 环境配置，不注入数据库连接池 provider（``db_pool_provider=None``），
    适用于不需要真实数据库的单元测试和 API 契约测试。

    Returns:
        配置好的 :class:`~fastapi.FastAPI` 实例，含健康检查路由和
        ``/api/v1`` 前缀
    """
    return create_app(settings=make_unit_test_settings())


# ---------------------------------------------------------------------------
# 可复用 fixture：client（SPEC §28、验收条件 5）
# ---------------------------------------------------------------------------


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """创建同步 HTTP 测试客户端。

    基于 :func:`app` fixture 创建 :class:`~fastapi.testclient.TestClient`，
    自动管理 Lifespan（启动时初始化资源，关闭时释放）。

    Returns:
        :class:`~fastapi.testclient.TestClient` 实例
    """
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 可复用 fixture：db_engine（SPEC §28.2、验收条件 5）
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    """创建连接到 Testcontainers PostgreSQL 18 的 AsyncEngine。

    从会话级 :func:`postgres_container` 获取连接 URL，创建引擎。
    测试函数结束后释放引擎资源。

    此 fixture 依赖 Docker（Testcontainers），仅用于集成测试。

    Yields:
        连接到测试容器的 :class:`~sqlalchemy.ext.asyncio.AsyncEngine` 实例
    """
    from app.infrastructure.database.engine import create_engine

    url = postgres_container.get_connection_url()
    engine = create_engine(url, pool_size=3, max_overflow=2)
    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# 可复用 fixture：db_session（SPEC §28.2、验收条件 5）
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """创建连接到 Testcontainers PostgreSQL 18 的独立 AsyncSession。

    每个测试函数获得一个全新的 AsyncSession（SPEC §5.6：禁止在并发任务间
    共享 AsyncSession）。测试结束后关闭会话，未提交的事务自动回滚。

    此 fixture 依赖 Docker（Testcontainers），仅用于集成测试。

    Yields:
        :class:`~sqlalchemy.ext.asyncio.AsyncSession` 实例
    """
    session = AsyncSession(db_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
