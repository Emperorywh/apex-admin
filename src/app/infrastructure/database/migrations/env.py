"""Alembic 异步迁移环境（SPEC §8.2、§5.4）。

配置要点：
- 使用异步 SQLAlchemy（``create_async_engine``）与 ``postgresql+psycopg`` 驱动（SPEC §5.4）
- 数据库 URL 从 :class:`~app.config.settings.Settings` 读取，不在此处硬编码（SPEC §8.2）
- 使用 ``connection.run_sync`` 模式在异步引擎上同步执行迁移逻辑

G1 阶段 ``target_metadata`` 为 ``None``——基座不使用 autogenerate，
各业务模块在自身迁移文件中手动编写表结构变更（SPEC §5.5、§8.2）。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings

# Alembic 运行时上下文（由 alembic CLI 注入）
config = context.config

# 从 alembic.ini 加载日志配置（仅在 alembic 命令执行期间生效）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata：G1 基座不使用 autogenerate（SPEC §8.2）
# 各业务模块在各自迁移文件中手动编写 DDL
target_metadata = None


def _get_database_url() -> str:
    """从部署配置读取数据库 URL（SPEC §8.2）。

    迁移环境与运行时应用共享同一套部署配置，
    确保迁移目标数据库与应用连接数据库一致。
    """
    return Settings().database_url


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库（SPEC §8.2）。

    Alembic 根据数据库 URL 的方言生成 DDL 文本，
    适用于 ``alembic upgrade head --sql`` 等场景。
    """
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在同步连接上配置迁移上下文并执行迁移。

    由 :func:`run_async_migrations` 通过 ``connection.run_sync`` 调用。
    """
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步模式：创建临时异步引擎并执行迁移（SPEC §5.4、§8.2）。

    使用 ``postgresql+psycopg`` 驱动和 ``NullPool``
    （迁移是短时一次性操作，无需连接池复用）。
    """
    url = _get_database_url()
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """在线模式入口：通过事件循环驱动异步迁移。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
