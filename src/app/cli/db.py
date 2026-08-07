"""db check / db upgrade 命令（SPEC §25.1）。

- ``db check``：检查数据库连接，成功返回 0，失败返回 1
- ``db upgrade``：只执行 ``alembic upgrade head``，不创建管理员或业务数据
"""

from __future__ import annotations

import asyncio
import sys

from alembic import command
from alembic.config import Config as AlembicConfig

from app.config.settings import Settings
from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider
from app.infrastructure.database.revision_check import SCRIPT_LOCATION


def db_check() -> int:
    """检查数据库连接并返回明确退出码（SPEC §25.1）。

    创建数据库连接池、执行 ``SELECT 1`` 连通性检查、释放连接池。
    成功返回 0，失败返回 1。连接池在 ``finally`` 中释放，不留半完成状态。

    Returns:
        0 表示数据库可连通，1 表示不可连通
    """
    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings 从环境变量加载
    return asyncio.run(_async_db_check(settings))


async def _async_db_check(settings: Settings) -> int:
    """db check 的异步实现。

    在独立连接池上执行连通性检查，无论检查结果或是否抛出异常，
    都在 ``finally`` 中释放连接池。
    """
    provider = SqlAlchemyDbPoolProvider(settings)
    try:
        await provider.initialize()
        connected = await provider.check_connection()
    finally:
        await provider.dispose()

    if connected:
        print("数据库连接正常")
        return 0
    print("数据库连接不可用", file=sys.stderr)
    return 1


def db_upgrade() -> int:
    """只执行 ``alembic upgrade head``（SPEC §25.1）。

    使用 Alembic command API 执行 ``upgrade head``。迁移环境（env.py）
    从部署配置读取数据库 URL。

    此命令不创建管理员或业务数据——仅执行迁移脚本中定义的 DDL。

    Returns:
        退出码：成功返回 0，迁移失败时异常传播由 CLI 入口处理
    """
    alembic_config = AlembicConfig()
    alembic_config.set_main_option("script_location", SCRIPT_LOCATION)
    command.upgrade(alembic_config, "head")
    print("数据库迁移完成：alembic upgrade head")
    return 0
