"""用例上下文装配测试 — SPEC 5.8 / 9.5.

覆盖:
  - UseCaseContext 由路由显式装配并传入 Use Case 的依赖模式。
  - Request ID 从请求上下文提取。
  - current_time 由 Clock Port 提供。
  - Actor 字段为 G2 占位（None）。
  - Clock Port 可被测试覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from starlette.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.application.context import UseCaseContext
from app.application.ports import Clock
from app.core.context.dependencies import create_use_case_context, get_clock


class _FixedClock(Clock):
    """返回固定时间的时钟实现 — 供测试注入。"""

    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed


def _create_context_test_app(clock: Clock | None = None) -> FastAPI:
    """创建带 UseCaseContext 装配的测试应用。"""

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    if clock is not None:
        app.dependency_overrides[get_clock] = lambda: clock

    @app.get("/api/v1/demo")
    async def demo_endpoint(
        ctx: Annotated[UseCaseContext, Depends(create_use_case_context)],
    ) -> dict[str, Any]:
        """使用 UseCaseContext 依赖的测试路由。

        演示 UseCaseContext 由路由显式装配并传入 Use Case 的模式：
        1. create_use_case_context 从请求提取 request_id 和 current_time。
        2. 路由获得装配好的 ctx，显式传递给 Use Case（此处直接返回验证）。
        """

        # 模拟 Use Case 调用 — ctx 被显式传递
        return {
            "request_id": ctx.request_id,
            "actor_id": ctx.actor_id,
            "session_id": ctx.session_id,
            "current_time": ctx.current_time.isoformat(),
        }

    return app


@pytest.mark.g1
@pytest.mark.api
def test_use_case_context_assembled_from_request() -> None:
    """路由通过依赖装配 UseCaseContext（SPEC 5.8 / 9.5）。

    create_use_case_context 依赖从请求 scope 提取 request_id，
    从 Clock Port 获取 current_time，构造不可变 UseCaseContext。
    """

    fixed_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    app = _create_context_test_app(clock=_FixedClock(fixed_time))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/demo",
            headers={"X-Request-ID": "test-req-ctx-001"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == "test-req-ctx-001"
    assert data["current_time"].startswith("2026-01-01T12:00:00")
    assert "+00:00" in data["current_time"]


@pytest.mark.g1
@pytest.mark.api
def test_use_case_context_actor_is_none_g2_placeholder() -> None:
    """G1 阶段 Actor 字段为 None（G2 占位）（SPEC 5.8）。"""

    app = _create_context_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/demo")

    data = response.json()
    assert data["actor_id"] is None
    assert data["session_id"] is None


@pytest.mark.g1
@pytest.mark.api
def test_use_case_context_request_id_generated_when_missing() -> None:
    """无入站 Request ID 头时使用中间件生成的值。"""

    app = _create_context_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/demo")

    data = response.json()
    # request_id 由中间件自动生成（非空）
    assert data["request_id"]
    assert len(data["request_id"]) > 0


@pytest.mark.g1
@pytest.mark.unit
def test_clock_dependency_overridable() -> None:
    """Clock Port 可通过 dependency_overrides 替换（SPEC 5.8）。

    测试通过 app.dependency_overrides 注入伪时钟实现，
    证明 Clock Port 是显式可替换的依赖。
    """

    fixed_time = datetime(2026, 6, 15, 8, 30, 0, tzinfo=UTC)
    app = FastAPI()
    app.dependency_overrides[get_clock] = lambda: _FixedClock(fixed_time)

    captured_time: list[datetime] = []

    @app.get("/api/v1/clock-test")
    async def clock_test(
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> dict[str, str]:
        captured_time.append(clock.now())
        return {"time": clock.now().isoformat()}

    with TestClient(app) as client:
        response = client.get("/api/v1/clock-test")

    assert response.status_code == 200
    assert captured_time[0] == fixed_time


@pytest.mark.g1
@pytest.mark.unit
def test_create_use_case_context_returns_immutable() -> None:
    """create_use_case_context 返回不可变 UseCaseContext。"""

    import dataclasses

    app = _create_context_test_app()
    with TestClient(app) as client:
        # 只需确认依赖能工作，不直接验证内部逻辑
        response = client.get("/api/v1/demo")

    assert response.status_code == 200
    # UseCaseContext 本身是不可变的（在 test_app_context 中已验证）
    ctx = UseCaseContext(request_id="test")
    assert ctx.__class__.__dataclass_params__.frozen is True
    assert dataclasses.is_dataclass(ctx)
