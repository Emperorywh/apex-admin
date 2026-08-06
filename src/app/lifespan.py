"""应用生命周期管理（SPEC §6.1）。

使用 FastAPI Lifespan（``asynccontextmanager``）提供启动和关闭生命周期管理，
不使用已废弃的事件式写法（``@app.on_event``）。

启动时：
- 校验必需配置已加载（Settings 构造时即已完成快速失败校验）
- 通过 provider 接口初始化数据库连接池（TASK-004 将填充具体实现）

关闭时：
- 释放数据库连接池和其他资源
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.settings import Settings
from app.health.providers import DbPoolProvider

_logger = logging.getLogger("app.lifespan")


@asynccontextmanager
async def lifespan(
    app: FastAPI,
    settings: Settings,
    db_pool_provider: DbPoolProvider | None,
) -> AsyncIterator[None]:
    """应用生命周期上下文管理器（SPEC §6.1）。

    Args:
        app: FastAPI 应用实例
        settings: 已校验的部署配置（必需配置缺失时已在 Settings 构造阶段快速失败）
        db_pool_provider: 数据库连接池 provider；为 None 时尚未配置数据库（TASK-004 填充）

    启动时通过 provider 接口初始化数据库连接池；数据库暂时不可用只影响就绪状态，
    不阻止应用启动（SPEC §6.1）。
    """
    _logger.info(
        "应用启动",
        extra={"app_env": settings.app_env.value},
    )

    # 通过 provider 接口初始化数据库连接池（SPEC §6.1）
    # TASK-004 将提供 DbPoolProvider 的具体实现
    if db_pool_provider is not None:
        await db_pool_provider.initialize()

    yield

    # 关闭时释放数据库连接池和其他资源（SPEC §6.1）
    if db_pool_provider is not None:
        await db_pool_provider.dispose()

    _logger.info("应用关闭")
