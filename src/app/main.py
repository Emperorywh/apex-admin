"""应用创建入口 — FastAPI 应用实例与 Lifespan 生命周期.

SPEC 6.1:
  - 提供统一的应用创建入口。
  - 使用 FastAPI Lifespan 提供启动和关闭生命周期管理。
  - 启动时校验必需配置（由 Settings 构造时完成）。
  - 启动时初始化数据库连接池；数据库暂时不可用只影响就绪状态，
    不得使存活检查失效。
  - 关闭时释放数据库连接池和其他资源。
  - 禁止在模块导入阶段执行数据库访问等隐式副作用。

本模块定义 ``create_app`` 工厂函数和 ``lifespan`` 异步上下文管理器。
模块导入时不实例化 Settings、不创建应用、不配置日志、不连接数据库，
确保 ``import app.main`` 零副作用。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.exception_handlers import register_exception_handlers
from app.api.health import router as health_router
from app.api.meta import router as meta_router
from app.api.metrics import router as metrics_router
from app.api.middleware import RequestContextMiddleware
from app.api.security import (
    RequestBodySizeMiddleware,
    SecurityHeadersMiddleware,
    TrustedProxyMiddleware,
)
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.metrics.middleware import MetricsMiddleware

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理 — 启动与关闭钩子.

    SPEC 6.1: 使用 Lifespan 提供启动和关闭管理，不使用已废弃的事件式写法。

    启动阶段:
      - 初始化数据库连接池（AsyncEngine）。
      - 创建健康检查器并绑定到应用状态。
      - 数据库暂时不可用只影响就绪状态，不阻塞启动。

    关闭阶段:
      - 释放数据库连接池（dispose engine）。
    """

    # 初始化生命周期事件追踪列表（供测试观察）
    app.state.lifecycle_events = []

    settings: Settings = app.state.settings

    # ── 启动钩子 ──
    app.state.lifecycle_events.append("startup")

    # 初始化数据库引擎与连接池（SPEC 6.1 / 8.1）
    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.core.metrics.db_events import register_db_metrics
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.health import DbHealthChecker
    from app.infrastructure.db.migrations import get_head_revision

    engine = create_db_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )
    app.state.db_engine = engine

    # 注册数据库指标事件监听器（SPEC 24.2: 连接池状态与慢查询识别）
    register_db_metrics(
        engine,
        slow_query_threshold_ms=settings.SLOW_QUERY_THRESHOLD_MS,
    )

    # 创建健康检查器 — 比较数据库 revision 与应用 head（SPEC 6.2）
    try:
        head_revision = get_head_revision(MODULE_VERSION_LOCATIONS)
    except Exception:
        # Alembic 配置不可用时 head 为空，健康检查将报告 revision 不一致
        head_revision = ""

    app.state.health_checker = DbHealthChecker(engine, head_revision)

    # ── 打印 API 文档地址（SPEC 9.6）──────────────────────────────────
    from app.core.api.openapi import resolve_docs_urls

    docs_urls = resolve_docs_urls(settings)
    if docs_urls["docs_url"] is None:
        _logger.info("API 文档端点已关闭")
    else:
        _logger.info(
            "API 文档端点已启用，请在服务地址后拼接以下路径访问",
            docs_url=docs_urls["docs_url"],
            redoc_url=docs_urls["redoc_url"],
            openapi_url=docs_urls["openapi_url"],
        )

    yield

    # ── 关闭钩子 ──
    # 释放数据库连接池（SPEC 6.1: 关闭时释放资源）
    await engine.dispose()
    app.state.lifecycle_events.append("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例.

    SPEC 6.1: 提供统一的应用创建入口。

    步骤:
      1. 加载部署配置（若未传入则显式构造 Settings）。
      2. 配置 structlog 结构化日志。
      3. 创建 FastAPI 应用，绑定 lifespan。
      4. 注册中间件（Request ID + 请求日志）。
      5. 挂载公共路由（health、meta 端点）。

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

    # OpenAPI 文档参数 — SPEC 9.6: 按模块 tag 分组，生产可关闭文档
    from app.core.api.openapi import build_openapi_kwargs

    openapi_kwargs = build_openapi_kwargs(settings)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        **openapi_kwargs,
    )

    # 将配置存入应用状态，供端点和中间件读取
    app.state.settings = settings

    # ── 注册中间件（SPEC 23.1 / 9.5 / 24.1）──────────────────────────
    #
    # Starlette 中间件按 LIFO 顺序处理请求: 后添加的中间件在最外层，
    # 先处理入站请求。注册顺序由内到外:
    #
    #   1. RequestContextMiddleware（最内层）— Request ID + 请求日志。
    #   2. TrustedProxyMiddleware — 可信代理头处理，存入 scope。
    #   3. RequestBodySizeMiddleware — 请求体大小限制，超限 413。
    #   4. SecurityHeadersMiddleware — 安全响应头。
    #   5. CORSMiddleware — CORS 预检与跨域头。
    #   6. TrustedHostMiddleware（最外层）— Host 白名单，最早拒绝。

    # 内层: Request ID 注入与请求日志（SPEC 9.5 / 24.1）
    app.add_middleware(RequestContextMiddleware)

    # 请求指标采集（SPEC 24.2: 请求数量/错误/耗时/慢接口识别）
    app.add_middleware(
        MetricsMiddleware,
        slow_request_threshold_ms=settings.SLOW_REQUEST_THRESHOLD_MS,
    )

    # 可信代理头处理 — 仅信任配置来源的 X-Forwarded-*（SPEC 23.1 / 26.3）
    app.add_middleware(
        TrustedProxyMiddleware,
        trusted_proxies=settings.trusted_proxy_list,
    )

    # 请求体大小限制 — 常规与上传分别限制（SPEC 23.1）
    app.add_middleware(
        RequestBodySizeMiddleware,
        max_request_size=settings.MAX_REQUEST_BODY_SIZE,
        max_upload_size=settings.MAX_UPLOAD_BODY_SIZE,
    )

    # 安全响应头（SPEC 23.1）
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS 白名单 — SPEC 23.1: "CORS 使用明确来源白名单"
    # 复用 ALLOWED_ORIGINS 作为 CORS 来源白名单。
    # 开发/测试环境默认 http://localhost，生产环境禁止通配。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origin_set),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 可信 Host 白名单 — SPEC 23.1: "配置可信 Host"
    # 最外层，最早拒绝非白名单 Host 请求。
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(settings.trusted_host_set),
    )

    # 注册异常处理器 — API 边界统一转换为 RFC 9457 problem+json（SPEC 9.3 / 10.1）
    register_exception_handlers(app)

    # 挂载路由 — health 端点挂载在根路径（SPEC 6.2）
    app.include_router(health_router)

    # /metrics 端点挂载在根路径（SPEC 24.2: 令牌保护，Nginx 不代理）
    app.include_router(metrics_router)

    # meta 端点使用 API 前缀（SPEC 9.1）
    app.include_router(meta_router, prefix=settings.API_PREFIX)

    # 业务模块路由 — 从显式模块清单装配（SPEC 5.5）
    #
    # 遍历 MODULE_MANIFEST 中所有已注册模块的 Router 列表，
    # 挂载到统一 API 前缀下。新增模块只需在 composition/modules.py
    # 的 MODULE_MANIFEST 中增加一项即可自动注册路由（SPEC 5.5）。
    from app.composition.modules import get_module_manifest

    for module_def in get_module_manifest():
        for module_router in module_def.routers:
            app.include_router(module_router, prefix=settings.API_PREFIX)

    return app
