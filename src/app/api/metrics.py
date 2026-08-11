"""/metrics 端点 — Prometheus 指标暴露与令牌访问控制.

SPEC 24.2: "指标接口受到访问限制"。

访问控制:
  - 通过 ``Authorization: Bearer <token>`` 头验证令牌。
  - 令牌来自部署配置 ``METRICS_TOKEN``（SPEC 24.2）。
  - 无有效令牌时返回 HTTP 403。

暴露边界约定:
  - Nginx 反向代理 **不**代理 ``/metrics`` 路径（见 deployment-conventions.md）。
  - Docker Compose 内 ``/metrics`` 仅通过内网可达。
  - 生产环境 ``METRICS_TOKEN`` 必须由环境变量设置。

不引入分布式链路追踪（SPEC 24.2: 不强制接入分布式链路追踪系统）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["metrics"])


def _check_token(request: Request) -> None:
    """验证 Bearer Token，无效时抛出 403.

    SPEC 24.2: "指标接口受到访问限制"。
    /metrics 端点仅接受与 ``METRICS_TOKEN`` 匹配的 Bearer Token。
    """

    settings = request.app.state.settings
    expected_token = settings.METRICS_TOKEN
    if expected_token is None:
        raise HTTPException(status_code=403)

    expected = expected_token.get_secret_value()

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=403)

    provided = auth_header[len("Bearer ") :]
    if provided != expected:
        raise HTTPException(status_code=403)


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus 指标端点 — SPEC 24.2.

    返回 prometheus_client 注册表的全部指标（含内置 Python 进程指标）。
    无有效令牌时返回 403。
    """

    _check_token(request)
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
