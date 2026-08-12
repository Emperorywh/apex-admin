"""公共 meta 端点 — 返回应用运行信息.

SPEC 6.1: 提供应用名称、版本、环境等运行信息。
SPEC 23.5: 公共接口必须显式声明。

此端点为显式公共端点，不需要认证。返回内容不包含敏感配置。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.api.schemas import ApiModel

router = APIRouter(tags=["meta"])


class MetaResponse(ApiModel):
    """应用元数据响应模型.

    返回应用名称、版本和当前运行环境。
    不包含任何敏感配置信息（SPEC 6.2: 健康检查返回内容不泄露敏感配置）。
    """

    model_config = {"extra": "forbid"}

    name: str
    version: str
    environment: str


@router.get("/meta", response_model=MetaResponse)
async def get_meta(request: Request) -> Any:
    """返回应用元数据.

    显式公共端点（SPEC 23.5），不需要认证。
    从应用状态读取配置信息返回。
    """

    settings = request.app.state.settings
    return MetaResponse(
        name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT.value,
    )
