"""Testcontainers PostgreSQL 18 fixture（SPEC §28.2）。

提供会话级 PostgreSQL 18 容器 fixture 和测试用 Settings 工厂，
供集成测试复用。禁止使用 SQLite 替代 PostgreSQL 执行集成测试
（SPEC §28.2）。

容器管理策略：
- 会话级作用域：整个测试会话共享一个容器实例，减少启停开销
- 测试结束后自动销毁容器数据（SPEC §28.2：测试结束后销毁容器数据）

前置条件：运行环境需要 Docker（Testcontainers 依赖）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.config.settings import AppEnv, Settings

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer

# PostgreSQL 18 镜像（SPEC §5.4：PostgreSQL 18.x）
_POSTGRES_IMAGE = "postgres:18"

# 测试用有效密钥（64 位 hex = 32 字节，字节值多样，非退化密钥）
# 三个密钥彼此独立，满足 Settings 的跨字段校验
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"


@pytest.fixture(scope="session")
def postgres_container() -> PostgresContainer:  # type: ignore[misc]
    """启动 PostgreSQL 18 容器，会话级共享（SPEC §28.2）。

    使用 ``driver='psycopg'`` 确保 :meth:`get_connection_url` 返回
    ``postgresql+psycopg://`` 格式的 URL（SPEC §5.4）。

    Yields:
        已启动的 :class:`~testcontainers.postgres.PostgresContainer` 实例，
        通过 :meth:`get_connection_url` 获取连接地址

    Teardown:
        会话结束后调用 :meth:`stop` 销毁容器，所有数据随之清除
        （SPEC §28.2：测试结束后销毁容器数据）
    """
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(_POSTGRES_IMAGE, driver="psycopg")
    container.start()
    yield container  # type: ignore[misc]
    container.stop()


def make_test_settings(database_url: str) -> Settings:
    """构造连接到指定数据库的测试 Settings。

    禁用 .env 加载，使用 testing 环境。三个 HMAC/加密密钥彼此独立，
    满足 Settings 的跨字段安全校验（SPEC §12.2、§23.2）。

    Args:
        database_url: PostgreSQL 连接 URL，格式
            ``postgresql+psycopg://<user>:<password>@<host>:<port>/<dbname>``

    Returns:
        配置好的 :class:`~app.config.settings.Settings` 实例
    """
    return Settings(
        _env_file=None,
        app_env=AppEnv.TESTING,
        database_url=database_url,
        access_token_hmac_key=_VALID_ACCESS_KEY,
        refresh_token_hmac_key=_VALID_REFRESH_KEY,
        config_encryption_key=_VALID_ENCRYPTION_KEY,
        file_storage_root="/tmp/apex-test-files",
    )
