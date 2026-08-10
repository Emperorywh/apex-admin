"""Alembic 迁移环境 — 异步 PostgreSQL（SPEC 8.2）.

职责:
  - 从 ``Settings`` 加载数据库 URL。
  - 从模块注册表收集 ``version_locations``（SPEC 5.5 / 8.2），
    仅使用注册表声明的路径，不硬编码或扫描。
  - 使用异步引擎执行迁移。

模块导入阶段不产生副作用（SPEC 6.1: 禁止在导入阶段执行数据库访问）。
迁移在 ``command.upgrade`` 时执行，此时 env.py 由 Alembic 运行时加载。
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# Windows 的 ProactorEventLoop 与 psycopg3 异步模式不兼容。
# 在执行异步迁移前切换为 SelectorEventLoop。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── Alembic 运行时上下文 ──────────────────────────────────────────────────
#
# ``config`` 是 Alembic 注入的 ``Config`` 实例，包含 alembic.ini 的选项。

config = context.config

# 配置日志（仅在通过 alembic.ini 启动时生效）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── 从 Settings 加载数据库 URL ────────────────────────────────────────────
#
# SPEC 6.1: 部署配置通过环境变量加载。
# env.py 在 Alembic 运行时执行，此时 ``Settings()`` 从环境变量读取配置。

from app.composition.modules import MODULE_VERSION_LOCATIONS  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.infrastructure.db.base import Base  # noqa: E402

_settings = Settings()
config.set_main_option("sqlalchemy.url", _settings.DATABASE_URL)

# ── 从模块注册表收集 version_locations（SPEC 8.2）─────────────────────────
#
# env.py 仅从模块注册表收集 version_locations，不硬编码路径。
# 当前无业务模块，MODULE_VERSION_LOCATIONS 为空列表。
# 默认 ``alembic/versions/`` 目录始终可用（Alembic 内置行为）。

if MODULE_VERSION_LOCATIONS:
    existing_locations = config.get_main_option("version_locations") or ""
    combined = (
        f"{existing_locations} {' '.join(MODULE_VERSION_LOCATIONS)}".strip()
        if existing_locations
        else " ".join(MODULE_VERSION_LOCATIONS)
    )
    config.set_main_option("version_locations", combined)

# ── target_metadata ────────────────────────────────────────────────────────
#
# 所有模块 ORM 模型继承自 ``Base``，autogenerate 通过 ``Base.metadata``
# 收集表结构。G1 阶段无业务表，metadata 为空。

target_metadata = Base.metadata


# ── 离线迁移 ───────────────────────────────────────────────────────────────


def run_migrations_offline() -> None:
    """离线模式 — 生成 SQL 脚本不连接数据库。"""

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ── 在线迁移 ───────────────────────────────────────────────────────────────


def do_run_migrations(connection: Connection) -> None:
    """配置迁移上下文并执行迁移。"""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步在线迁移 — 创建临时引擎，执行迁移后释放。"""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式 — 通过异步引擎连接数据库执行迁移。"""

    asyncio.run(run_async_migrations())


# ── 入口 ───────────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
