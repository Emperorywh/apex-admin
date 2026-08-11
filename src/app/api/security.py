"""HTTP 安全中间件 — SPEC 23.1 / 26.3.

实现 SPEC 23.1 的 HTTP 安全基线:

  - 安全响应头: X-Content-Type-Options、X-Frame-Options、Referrer-Policy。
  - 请求体大小限制: 常规请求与上传请求分别限制，超限返回 413。
  - 可信代理头处理: 仅当请求来源在配置的可信代理列表中时，
    X-Forwarded-* 代理头才被采信，防止伪造。

SPEC 26.3: "API 容器只接受来自 Nginx 网络的代理流量，
并只信任 Nginx 的代理头"。

所有中间件均通过 ``create_app`` 注册，不在模块导入阶段产生副作用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.types import ASGIApp

# ── 安全响应头常量 ──────────────────────────────────────────────────────────
#
# SPEC 23.1: "设置必要的安全响应头"。
# 以下头附加到每个响应，提供浏览器级安全防护:
#   - X-Content-Type-Options: nosniff — 禁止 MIME 嗅探。
#   - X-Frame-Options: DENY — 禁止页面被嵌入 iframe（防点击劫持）。
#   - Referrer-Policy: strict-origin-when-cross-origin — 限制 Referer 泄漏。

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

#: Content-Type 前缀，用于识别上传请求（multipart/form-data）。
_MULTIPART_PREFIX = "multipart/"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件 — SPEC 23.1.

    向每个响应附加标准安全头，防止 MIME 嗅探、点击劫持和 Referrer 泄漏。
    不覆盖已有响应头（尊重下游显式设置的同名头）。
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """处理请求并附加安全响应头."""

        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            # 仅在响应未已有同名头时设置，尊重下游显式配置。
            if header not in response.headers:
                response.headers[header] = value
        return response


class RequestBodySizeMiddleware(BaseHTTPMiddleware):
    """请求体大小限制中间件 — SPEC 23.1.

    根据 Content-Length 检查请求体大小:
      - 上传请求（multipart/form-data）适用 ``max_upload_size``。
      - 其他请求适用 ``max_request_size``。

    超限时返回 HTTP 413（Payload Too Large）。

    SPEC 23.1: "限制请求体大小"、"对上传接口使用更严格限制"。
    """

    def __init__(
        self,
        app: ASGIApp,
        max_request_size: int,
        max_upload_size: int,
    ) -> None:
        """初始化请求体大小限制中间件.

        参数:
            app: ASGI 应用（由 Starlette 传入）。
            max_request_size: 常规请求体大小上限（字节）。
            max_upload_size: 上传请求体大小上限（字节）。
        """

        super().__init__(app)
        self._max_request_size = max_request_size
        self._max_upload_size = max_upload_size

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """检查 Content-Length 并在超限时返回 413."""

        content_length_str = request.headers.get("content-length")
        if content_length_str is not None:
            try:
                content_length = int(content_length_str)
            except ValueError:
                # 非法 Content-Length 交给下游处理
                return await call_next(request)

            content_type = request.headers.get("content-type", "")
            limit = (
                self._max_upload_size
                if content_type.startswith(_MULTIPART_PREFIX)
                else self._max_request_size
            )

            if content_length > limit:
                return JSONResponse(
                    status_code=413,
                    content={
                        "type": "about:blank",
                        "title": "Payload Too Large",
                        "status": 413,
                        "detail": (
                            f"请求体大小 {content_length} 字节超过限制 {limit} 字节"
                        ),
                    },
                    media_type="application/problem+json",
                )

        return await call_next(request)


class TrustedProxyMiddleware(BaseHTTPMiddleware):
    """可信代理头处理中间件 — SPEC 23.1 / 26.3.

    仅当请求的直接来源 IP 在配置的可信代理列表中时，
    X-Forwarded-For 和 X-Forwarded-Proto 代理头才被采信。

    采信时:
      - 从 X-Forwarded-For 提取最左侧（最原始）客户端 IP，
        存入 ``scope["trusted_client_ip"]``。
      - 从 X-Forwarded-Proto 提取原始协议，
        存入 ``scope["trusted_scheme"]``。

    不采信时:
      - ``scope["trusted_client_ip"]`` 为直接来源 IP。
      - ``scope["trusted_scheme"]`` 为 ASGI 连接的实际协议。
      - X-Forwarded-* 头被完全忽略，防止伪造。

    SPEC 23.1: "正确处理可信反向代理头"。
    SPEC 26.3: "API 容器只接受来自 Nginx 网络的代理流量，
    并只信任 Nginx 的代理头"。
    """

    def __init__(
        self,
        app: ASGIApp,
        trusted_proxies: frozenset[str],
    ) -> None:
        """初始化可信代理头中间件.

        参数:
            app: ASGI 应用（由 Starlette 传入）。
            trusted_proxies: 可信代理 IP 集合。空集合表示不信任任何代理。
        """

        super().__init__(app)
        self._trusted_proxies = trusted_proxies

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """根据来源是否可信，决定是否采纳代理头."""

        peer_ip = ""
        if request.client is not None:
            peer_ip = request.client.host

        if peer_ip in self._trusted_proxies:
            # 来源可信 — 采纳 X-Forwarded-* 头
            forwarded_for = request.headers.get("x-forwarded-for", "")
            # X-Forwarded-For 格式: "client, proxy1, proxy2"
            # 最左侧为最原始客户端 IP
            client_ip = (
                forwarded_for.split(",")[0].strip()
                if forwarded_for.strip()
                else peer_ip
            )
            scheme = request.headers.get("x-forwarded-proto", "")
            if not scheme:
                scheme = request.url.scheme
        else:
            # 来源不可信 — 忽略代理头，使用直接连接信息
            client_ip = peer_ip
            scheme = request.url.scheme

        request.scope["trusted_client_ip"] = client_ip
        request.scope["trusted_scheme"] = scheme

        return await call_next(request)
