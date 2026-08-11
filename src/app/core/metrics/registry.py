"""Prometheus 运行指标定义 — SPEC 24.2.

定义应用的 Prometheus 指标（注册到 prometheus_client 默认注册表）:

HTTP 请求指标:
  - ``apex_http_requests_total`` — 请求计数（Counter）。
  - ``apex_http_request_errors_total`` — 错误请求计数（Counter，状态码 ≥ 400）。
  - ``apex_http_request_duration_seconds`` — 请求耗时直方图（Histogram）。

数据库连接池指标:
  - ``apex_db_pool_checked_out_connections`` — 当前从连接池检出的连接数（Gauge）。

/metrics 端点通过 ``generate_latest()`` 暴露全部注册表内容，
包括 prometheus_client 内置的 Python 进程指标。

不引入分布式链路追踪（SPEC 24.2: 不强制接入分布式链路追踪系统）。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── HTTP 请求指标（SPEC 24.2: "可以获取请求数量、错误数量和请求耗时"）──────────

#: HTTP 请求总数 — 按方法、端点模板和状态码分区。
HTTP_REQUESTS_TOTAL = Counter(
    "apex_http_requests_total",
    "HTTP requests processed, partitioned by method, endpoint and status code.",
    labelnames=["method", "endpoint", "status"],
)

#: HTTP 请求错误数 — 仅统计状态码 ≥ 400 的请求。
HTTP_REQUEST_ERRORS_TOTAL = Counter(
    "apex_http_request_errors_total",
    "HTTP requests with error status code (>= 400).",
    labelnames=["method", "endpoint"],
)

#: HTTP 请求耗时直方图（秒）。
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "apex_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    labelnames=["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ── 数据库连接池指标（SPEC 24.2: "可以监控数据库连接池状态"）──────────────────

#: 当前从连接池检出的连接数 — 通过 pool checkout/checkin 事件维护。
DB_POOL_CHECKED_OUT = Gauge(
    "apex_db_pool_checked_out_connections",
    "Number of database connections currently checked out from the pool.",
)
