"""SQLAlchemy 2.0 异步引擎工厂 — SPEC 8.1.

创建 ``AsyncEngine`` 实例，配置连接池参数。

SPEC 26.1 容量基线（注释说明）:
  - 默认 ``pool_size=5``、``max_overflow=5``，即每 Worker 峰值 10 个连接。
  - 默认 API Worker 数量为 2，API 侧峰值合计 20 个连接。
  - 修改前必须完成容量计算:
    ``Worker × (pool_size + max_overflow) + 预留 ≤ max_connections``
    ``- 监控预留``

``pool_pre_ping=True`` 使引擎在使用连接前先执行轻量探测，
当数据库从不可用恢复后无需重启进程即可重新获取连接（SPEC 6.2:
"恢复后无需重启进程即可重新就绪"）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# SPEC 26.1 默认容量基线
DEFAULT_POOL_SIZE: int = 5
DEFAULT_MAX_OVERFLOW: int = 5

# 默认连接超时（秒）— 防止数据库不可用时连接无限等待
DEFAULT_CONNECT_TIMEOUT: int = 10


def create_db_engine(
    database_url: str,
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
    connect_args: dict[str, object] | None = None,
) -> AsyncEngine:
    """创建异步数据库引擎.

    参数:
        database_url: SQLAlchemy 异步连接 URL
            （格式 ``postgresql+psycopg://...``，SPEC 8.1）。
        pool_size:    连接池常驻连接数（SPEC 26.1 默认 5）。
        max_overflow: 连接池溢出上限（SPEC 26.1 默认 5）。
        connect_args: 传递给 psycopg3 的额外连接参数，
            覆盖默认的 ``connect_timeout``。

    返回:
        配置完成的 ``AsyncEngine`` 实例。
    """

    # 默认 connect_timeout 防止数据库不可用时连接无限挂起
    # （SPEC 6.2: 数据库不可用应快速影响就绪状态）。
    merged_connect_args: dict[str, object] = {
        "connect_timeout": DEFAULT_CONNECT_TIMEOUT,
    }
    if connect_args:
        merged_connect_args.update(connect_args)

    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        connect_args=merged_connect_args,
    )
