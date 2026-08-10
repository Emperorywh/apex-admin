"""用例上下文装配 — SPEC 5.8 / 9.5.

SPEC 5.8: "Router 将认证结果转换为不可变 ``UseCaseContext``，
显式传给 Use Case"。

本模块提供 FastAPI 依赖，从请求上下文装配 ``UseCaseContext``：
  - request_id 从中间件存入的 scope 中提取（SPEC 9.5）。
  - current_time 由 Clock Port 获取（SPEC 5.8）。
  - actor_id / session_id 为 G2 占位（None），G2 认证实现后填充。

路由层通过 ``Depends(create_use_case_context)`` 获取装配好的上下文，
显式传递给 Use Case，不依赖隐式全局状态（SPEC 5.8 / 9.5）。

公开 API:
  - ``get_clock``: 默认 Clock Port 提供者（返回 SystemClock）。
  - ``create_use_case_context``: 从请求装配 UseCaseContext 的依赖。
"""

from app.core.context.dependencies import create_use_case_context, get_clock

__all__ = ["create_use_case_context", "get_clock"]
