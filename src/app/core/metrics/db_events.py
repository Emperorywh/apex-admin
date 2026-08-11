"""SQLAlchemy 事件监听器 — 数据库连接池状态与慢查询识别.

SPEC 24.2:
  - "可以监控数据库连接池状态" — 通过 pool checkout/checkin 事件维护 Gauge。
  - "可以识别慢数据库操作" — 通过 before/after_cursor_execute 事件计时，
    超过阈值时记录结构化 ``slow_query`` 警告日志。

事件注册在引擎创建后由 ``lifespan`` 调用 ``register_db_metrics`` 完成。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import event

from app.core.metrics.registry import DB_POOL_CHECKED_OUT

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_logger = structlog.get_logger().bind(module="app.core.metrics.db_events")

#: 存储在 ``ExecutionContext`` 上的查询开始时间属性名。
_QUERY_START_ATTR = "_apex_query_start_time"


def register_db_metrics(
    engine: AsyncEngine,
    *,
    slow_query_threshold_ms: int = 500,
) -> None:
    """在异步引擎的底层同步引擎和连接池上注册指标事件监听器.

    参数:
        engine: 异步 SQLAlchemy 引擎。
        slow_query_threshold_ms: 慢查询阈值（毫秒），超过时记录
            结构化 ``slow_query`` 警告日志。
    """

    sync_engine = engine.sync_engine

    # ── 连接池状态（SPEC 24.2: 可以监控数据库连接池状态）─────────────────
    event.listen(sync_engine.pool, "checkout", _on_pool_checkout)
    event.listen(sync_engine.pool, "checkin", _on_pool_checkin)

    # ── 慢查询识别（SPEC 24.2: 可以识别慢数据库操作）─────────────────────
    def on_before_cursor(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        setattr(context, _QUERY_START_ATTR, time.perf_counter())

    def on_after_cursor(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        start = getattr(context, _QUERY_START_ATTR, None)
        if start is None:
            return
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        if duration_ms > slow_query_threshold_ms:
            preview = (statement or "")[:200]
            _logger.warning(
                "slow_query",
                duration_ms=duration_ms,
                threshold_ms=slow_query_threshold_ms,
                statement_preview=preview,
            )

    event.listen(sync_engine, "before_cursor_execute", on_before_cursor)
    event.listen(sync_engine, "after_cursor_execute", on_after_cursor)


def _on_pool_checkout(
    dbapi_conn: Any,
    connection_record: Any,
    connection_proxy: Any,
) -> None:
    """连接池检出事件 — 检出连接数 +1。"""

    DB_POOL_CHECKED_OUT.inc()


def _on_pool_checkin(
    dbapi_conn: Any,
    connection_record: Any,
) -> None:
    """连接池归还事件 — 检出连接数 -1。"""

    DB_POOL_CHECKED_OUT.dec()
