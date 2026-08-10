"""用例上下文装配依赖 — SPEC 5.8 / 9.5.

提供 FastAPI 依赖函数，从 HTTP 请求上下文装配不可变 ``UseCaseContext``。
路由层通过 ``Depends`` 获取上下文后显式传递给 Use Case，
不依赖 ContextVar 或全局变量承载业务状态（SPEC 5.8）。

依赖模式::

    @router.get("/items")
    async def list_items(
        ctx: Annotated[UseCaseContext, Depends(create_use_case_context)],
    ):
        return await item_use_case.list_items(ctx, ...)

G1 阶段 Actor 字段为 None（G2 认证占位），G2 实现后认证依赖填充
actor_id 和 session_id。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.application.context import UseCaseContext
from app.application.ports import Clock, SystemClock


def get_clock() -> Clock:
    """默认 Clock Port 提供者 — SPEC 5.8.

    返回 ``SystemClock`` 实例。测试中可通过
    ``app.dependency_overrides[get_clock] = ...`` 替换为伪实现。
    """

    return SystemClock()


def create_use_case_context(
    request: Request,
    clock: Annotated[Clock, Depends(get_clock)],
) -> UseCaseContext:
    """从请求装配不可变 ``UseCaseContext`` — SPEC 5.8 / 9.5.

    提取请求上下文中的 Request ID 和当前时间，构造 ``UseCaseContext``。
    Actor ID 和 Session ID 为 G2 占位（None），G2 认证实现后由
    认证依赖填充。

    参数:
        request: FastAPI 请求对象（scope 中含 request_id）。
        clock:   Clock Port 实例（通过 ``get_clock`` 依赖注入）。

    返回:
        装配好的不可变 ``UseCaseContext``。
    """

    # Request ID 由 RequestContextMiddleware 存入 scope（SPEC 9.5）。
    raw_request_id = request.scope.get("request_id", "")
    request_id = str(raw_request_id) if raw_request_id else ""

    return UseCaseContext(
        request_id=request_id,
        actor_id=None,  # G2 认证占位
        session_id=None,  # G2 会话占位
        current_time=clock.now(),
    )
