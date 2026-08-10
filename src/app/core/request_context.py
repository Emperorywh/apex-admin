"""请求上下文 — ContextVar 仅用于日志关联.

SPEC 5.8: "ContextVar 只允许用于日志关联，不得作为业务授权、
事务或领域状态的数据源。"

本模块定义的唯一 ContextVar ``request_id_var`` 仅供日志处理器
读取以将 Request ID 注入结构化日志。中间件在请求开始时设置，
请求结束后自动随协程上下文回收。

禁止在此模块或任何其他位置通过此 ContextVar 读取业务数据。
"""

from __future__ import annotations

from contextvars import ContextVar

# ContextVar 仅用于日志关联（SPEC 5.8 静态约束）。
# 中间件在请求处理前 set，structlog 处理器在日志输出时 get。
# 默认空字符串表示当前无活跃请求。
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
