"""Origin 校验中间件——CSRF 防护（SPEC §12.4）。

Refresh、Logout 等读取 Cookie 的状态变更接口必须校验 ``Origin`` 是否
精确匹配部署配置白名单（SPEC §12.4）。这防止跨站请求伪造（CSRF）攻击。

G2 默认只支持前端与 API 同站部署（SPEC §12.4）；跨站部署必须新增安全
ADR 和对应 CSRF 集成测试。
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.config.settings import Settings


def validate_origin(request: Request, settings: Settings) -> None:
    """校验请求的 ``Origin`` 头是否精确匹配部署配置白名单（SPEC §12.4）。

    状态变更接口（Refresh、Logout）在端点函数开头调用此函数。

    - ``Origin`` 缺失 → 拒绝（403）
    - ``Origin`` 不在白名单 → 拒绝（403）
    - ``Origin`` 精确匹配白名单 → 通过

    精确匹配意味着完整 URL 必须完全一致（scheme + host + port），
    不支持通配符或子域匹配（SPEC §12.4：精确匹配部署配置白名单）。

    Args:
        request: 当前 HTTP 请求
        settings: 部署配置（提供 ``allowed_origins`` 白名单）

    Raises:
        HTTPException: ``Origin`` 缺失或不匹配时返回 403
    """
    origin = request.headers.get("origin")
    if origin is None or origin not in settings.allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin 不被允许",
        )
