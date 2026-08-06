"""健康检查路由（SPEC §6.2）。

存活检查和就绪检查端点实现。存活检查不依赖任何外部资源，就绪检查通过
provider 接口检查 DB 连接和 Alembic revision 一致性。

健康检查路由不挂载在 ``/api/v1`` 前缀下，允许反向代理和进程管理器
直接访问（SPEC §6.2）。
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Response, status
from starlette.requests import Request

from app.health.providers import DbPoolProvider, ReadinessProbe

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", summary="存活检查")
async def live() -> dict[str, str]:
    """存活检查（SPEC §6.2）。

    只验证进程事件循环可响应，数据库不可用时仍返回 HTTP 200。
    """
    return {"status": "ok"}


@router.get("/ready", summary="就绪检查")
async def ready(request: Request, response: Response) -> dict[str, str]:
    """就绪检查（SPEC §6.2）。

    通过 provider 接口检查 DB 连接和 Alembic revision 一致性。
    任一条件失败时返回 HTTP 503；恢复后无需重启进程即可重新就绪。

    返回内容不泄露敏感配置。
    """
    all_ready = True

    # 数据库连通性检查（TASK-004 提供具体实现）
    db_pool = cast("DbPoolProvider | None", getattr(request.app.state, "db_pool_provider", None))
    if db_pool is not None:
        db_ok = await db_pool.check_connection()
        if not db_ok:
            all_ready = False

    # Alembic revision 一致性校验（TASK-005 提供具体实现）
    revision_probe = cast(
        "ReadinessProbe | None",
        getattr(request.app.state, "revision_probe", None),
    )
    if revision_probe is not None:
        revision_ok = await revision_probe.probe()
        if not revision_ok:
            all_ready = False

    if not all_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready"}

    return {"status": "ok"}
