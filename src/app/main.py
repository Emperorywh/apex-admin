"""应用创建入口 — FastAPI 应用实例与 Lifespan 生命周期.

SPEC 6.1:
  - 提供统一的应用创建入口。
  - 使用 FastAPI Lifespan 提供启动和关闭生命周期管理。
  - 启动时校验必需配置（由 Settings 构造时完成）。
  - 关闭时释放资源。
  - 禁止在模块导入阶段执行数据库访问等隐式副作用。

本模块定义 ``create_app`` 工厂函数和 ``lifespan`` 异步上下文管理器。
模块导入时不实例化 Settings、不创建应用、不配置日志、不连接数据库，
确保 ``import app.main`` 零副作用。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from app.api.meta import router as meta_router
from app.api.middleware import RequestContextMiddleware
from app.core.config import Settings
from app.core.logging import configure_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理 — 启动与关闭钩子.

    SPEC 6.1: 使用 Lifespan 提供启动和关闭管理，不使用已废弃的事件式写法。

    启动阶段:
      - 标记应用已启动。
      - 数据库连接池初始化由 TASK-004 实现。

    关闭阶段:
      - 标记应用已关闭。
      - 数据库连接池释放由 TASK-004 实现。

    可通过 ``app.state.lifecycle_events`` 列表观察启动/关闭执行顺序，
    便于测试验证生命周期钩子（SPEC 6.1: lifespan 启动/关闭钩子可测）。
    """

    # 初始化生命周期事件追踪列表（供测试观察）
    app.state.lifecycle_events = []

    # ── 启动钩子 ──
    app.state.lifecycle_events.append("startup")
    # TASK-004: 在此初始化数据库连接池

    yield

    # ── 关闭钩子 ──
    # TASK-004: 在此释放数据库连接池和其他资源
    app.state.lifecycle_events.append("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例.

    SPEC 6.1: 提供统一的应用创建入口。

    步骤:
      1. 加载部署配置（若未传入则显式构造 Settings）。
      2. 配置 structlog 结构化日志。
      3. 创建 FastAPI 应用，绑定 lifespan。
      4. 注册中间件（Request ID + 请求日志）。
      5. 挂载公共路由（meta 端点）。

    参数:
        settings: 部署配置实例。传入时直接使用，不传入时显式构造
                  ``Settings()``（从环境变量加载）。

    返回:
        配置完成的 FastAPI 应用实例。
    """

    # 显式构造 Settings，触发类型校验和生产安全检查
    if settings is None:
        settings = Settings()

    # 配置结构化日志（SPEC 24.1 / 24.3）
    configure_logging(settings)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )

    # 将配置存入应用状态，供端点和中间件读取
    app.state.settings = settings

    # 注册中间件 — Request ID 注入与请求日志（SPEC 9.5 / 24.1）
    app.add_middleware(RequestContextMiddleware)

    # 挂载路由 — meta 端点使用 API 前缀（SPEC 9.1）
    app.include_router(meta_router, prefix=settings.API_PREFIX)

    return app
