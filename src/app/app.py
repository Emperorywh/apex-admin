"""应用工厂（SPEC §6.1）。

提供统一的应用创建入口 :func:`create_app`，返回配置好的 FastAPI 实例。

应用配置：
- 统一 API 前缀 ``/api/v1``（SPEC §9.1）
- 健康检查路由挂载在根路径（``/health/live``、``/health/ready``），不在 API 前缀下
- Lifespan 管理启动和关闭资源（SPEC §6.1）
- 结构化 JSON 日志（SPEC §24.1）
- Request ID 中间件（SPEC §9.5）

数据库连接池和 Alembic revision 校验通过 provider 接口注入，
TASK-004 和 TASK-005 分别提供具体实现。
"""

from __future__ import annotations

import functools

from fastapi import APIRouter, FastAPI

from app.api.handlers import register_exception_handlers
from app.config.settings import Settings
from app.health.providers import DbPoolProvider, ReadinessProbe
from app.health.routes import router as health_router
from app.lifespan import lifespan
from app.logging import configure_logging
from app.middleware.request_id import RequestIdMiddleware

# 应用名称和版本（SPEC §6.1：提供应用名称、版本、环境等运行信息）
APP_NAME = "Apex Admin"
APP_VERSION = "0.1.0"

# API 统一前缀（SPEC §9.1）
API_PREFIX = "/api/v1"


def create_app(
    settings: Settings,
    *,
    db_pool_provider: DbPoolProvider | None = None,
    revision_probe: ReadinessProbe | None = None,
) -> FastAPI:
    """创建并配置 FastAPI 应用实例（SPEC §6.1）。

    Args:
        settings: 已校验的部署配置
        db_pool_provider: 数据库连接池 provider；TASK-004 填充具体实现，
            为 None 时不初始化数据库（用于测试或过渡阶段）
        revision_probe: Alembic revision 一致性校验探针；TASK-005 填充具体实现，
            为 None 时不校验迁移版本

    Returns:
        配置好的 FastAPI 实例，带 ``/api/v1`` 前缀和健康检查路由
    """
    # 配置结构化 JSON 日志（SPEC §24.1）
    configure_logging(settings)

    # 使用 functools.partial 绑定 settings 和 provider 到 lifespan
    # FastAPI 的 lifespan 参数接受 (app) -> AsyncIterator 的可调用对象
    lifespan_factory = functools.partial(
        lifespan, settings=settings, db_pool_provider=db_pool_provider
    )

    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        summary="FastAPI 后台管理系统 API 基座",
        lifespan=lifespan_factory,
    )

    # 存储配置和 provider 到 app.state，供健康检查和后续模块使用
    app.state.settings = settings
    app.state.db_pool_provider = db_pool_provider
    app.state.revision_probe = revision_probe

    # 注册 RFC 9457 异常处理器（SPEC §10.1：在 API 边界统一完成异常到 HTTP 响应的转换）
    register_exception_handlers(app)

    # 注册中间件（Request ID 生成、响应头写入、请求日志）
    app.add_middleware(RequestIdMiddleware)

    # 注册健康检查路由（不在 /api/v1 前缀下，允许反向代理和进程管理器直接访问）
    app.include_router(health_router)

    # 注册 API 路由（统一 /api/v1 前缀，后续业务模块挂载于此）
    api_router = APIRouter(prefix=API_PREFIX)
    app.include_router(api_router)

    return app
