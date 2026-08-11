"""运行指标采集包 — SPEC 24.2.

提供:
  - Prometheus 指标定义（registry）
  - 请求指标采集中间件（middleware）
  - 数据库连接池与慢查询事件监听器（db_events）

不接入分布式链路追踪（SPEC 24.2: 不强制接入分布式链路追踪系统）。
"""
