"""SQLAlchemy 异步引擎与连接池（SPEC §8.1、§5.4）。

使用 ``create_async_engine`` 和 ``postgresql+psycopg`` 驱动创建异步引擎，
连接池参数（``pool_size``、``max_overflow``）可配置（SPEC §26.1）。

连接池容量预算必须满足（SPEC §26.1）：
``API Worker × 每 Worker (pool_size + max_overflow) + 预留 ≤ PostgreSQL max_connections``
默认 pool_size=5、max_overflow=5（SPEC §26.1）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# 默认连接池参数（SPEC §26.1：每 Worker pool_size=5、max_overflow=5）
DEFAULT_POOL_SIZE: int = 5
DEFAULT_MAX_OVERFLOW: int = 5


def create_engine(
    database_url: str,
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
    pool_pre_ping: bool = True,
) -> AsyncEngine:
    """创建 SQLAlchemy 异步引擎（SPEC §8.1）。

    使用 ``postgresql+psycopg`` 驱动（psycopg3 原生异步支持），
    配置连接池参数和连接健康检查。

    Args:
        database_url: PostgreSQL 连接 URL，格式
            ``postgresql+psycopg://<user>:<password>@<host>:<port>/<dbname>``
        pool_size: 连接池保持的常驻连接数（SPEC §26.1）
        max_overflow: 连接池允许的溢出连接数，即峰值连接数为
            ``pool_size + max_overflow``（SPEC §26.1）
        pool_pre_ping: 是否在从连接池借用连接前执行健康检查。
            启用后可避免使用已断开的连接（推荐生产环境开启）

    Returns:
        配置好的 :class:`~sqlalchemy.ext.asyncio.AsyncEngine` 实例，
        供 Session 工厂和 UoW 创建 AsyncSession

    Raises:
        ValueError: database_url 不是 postgresql+psycopg 协议
    """
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError(
            "database_url 必须使用 postgresql+psycopg 协议"
            "（SPEC §5.4：PostgreSQL 驱动固定为 psycopg 3.3.x）"
        )

    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=pool_pre_ping,
    )
