"""健康检查端点 — SPEC 6.2.

SPEC 6.2:
  - ``GET /health/live`` 只验证进程事件循环可响应，
    数据库不可用时仍返回 HTTP 200。
  - ``GET /health/ready`` 验证数据库连接和 Alembic revision 一致性；
    任一失败时返回 HTTP 503 和稳定错误码。
  - 响应内容不泄露敏感配置。

通过 ``app.state.health_checker``（HealthCheck Port 实例）执行检查，
API 层不直接依赖 SQLAlchemy 或 Alembic（SPEC 5.2 分层约束）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from app.application.ports import HealthCheck, HealthResult

router = APIRouter(tags=["health"])

# 健康检查响应不含任何敏感字段（SPEC 6.2）。
# 只暴露 healthy、code 和 detail，不包含 DATABASE_URL、密钥等配置。


def _result_to_response(result: HealthResult) -> JSONResponse:
    """将 ``HealthResult`` 转换为 HTTP 响应.

    - ``healthy=True``  → HTTP 200
    - ``healthy=False`` → HTTP 503
    """

    status_code = 200 if result.healthy else 503
    body: dict[str, Any] = {
        "status": "healthy" if result.healthy else "unhealthy",
        "code": result.code,
        "detail": result.detail,
    }
    return JSONResponse(status_code=status_code, content=body)


def _get_health_checker(request: Request) -> HealthCheck:
    """从应用状态获取健康检查器实例.

    SPEC 5.2: API 层通过 Port 调用，不直接依赖 Infrastructure。
    使用 ``cast`` 保持类型安全。
    """

    return cast("HealthCheck", request.app.state.health_checker)


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    """/health/live — 存活检查（SPEC 6.2）.

    只验证进程事件循环可响应，不检查数据库。
    数据库不可用时仍返回 HTTP 200。
    """

    return {"status": "healthy"}


@router.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    """/health/ready — 就绪检查（SPEC 6.2）.

    验证数据库连接和 Alembic revision 一致性。
    任一失败时返回 HTTP 503 和稳定错误码。
    恢复后无需重启进程即可重新就绪（SPEC 6.2）。
    """

    checker = _get_health_checker(request)
    result = await checker.check_ready()
    return _result_to_response(result)
