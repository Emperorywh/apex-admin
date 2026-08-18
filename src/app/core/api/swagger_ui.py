"""自定义 Swagger UI 文档页 — SPEC 9.6.

在默认 Swagger UI 基础上增强文档页的可用性:

  1. 接口搜索 — 启用 Swagger UI 内置过滤栏，按 tag / 路径 / 摘要过滤接口。
  2. 单个 API 文档复制 — 每个接口卡片提供"复制文档"按钮，
     生成该接口的 Markdown 文档（参数表 / 请求体示例 / 响应说明）。
  3. 全局参数设置 — 顶栏面板维护全局 header / query 参数，
     "Try it out" 发出的所有请求自动携带，配置保存在浏览器 localStorage。

实现方式: 用自定义 HTML 页面（``swagger_ui.html``）替换 FastAPI 默认的
``/docs`` 路由；ReDoc 与 OpenAPI JSON 端点保持不变。文档端点关闭时
本模块不注册任何路由，生产环境的关闭行为（SPEC 9.6）不受影响。
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.responses import HTMLResponse
from starlette.routing import Route

from app.core.api.openapi import resolve_docs_urls

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.responses import Response

    from app.core.config import Settings

#: 文档页 HTML 模板 — 与本模块同目录。
_TEMPLATE_PATH = Path(__file__).parent / "swagger_ui.html"


def register_custom_swagger_ui(app: FastAPI, settings: Settings) -> None:
    """用增强版 Swagger UI 页面替换默认 ``/docs`` 路由 — SPEC 9.6.

    参数:
        app: 待配置的 FastAPI 应用实例。
        settings: 部署配置实例（提供应用名称与 CDN 地址）。

    说明:
        - 文档端点关闭（生产环境或 ``ENABLE_API_DOCS=False``）时不注册
          任何路由，保持默认的 404 行为。
        - 仅替换 Swagger UI 页面；``/redoc`` 与 ``/openapi.json`` 不变。
    """

    docs_urls = resolve_docs_urls(settings)
    docs_url = docs_urls["docs_url"]
    openapi_url = docs_urls["openapi_url"]
    if docs_url is None or openapi_url is None:
        return

    page_html = (
        _TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("{{ APP_TITLE }}", html.escape(settings.APP_NAME))
        .replace("{{ OPENAPI_URL }}", openapi_url)
        .replace("{{ SWAGGER_CDN_BASE }}", settings.SWAGGER_CDN_BASE.rstrip("/"))
    )

    async def custom_swagger_ui(request: Request) -> Response:  # noqa: ARG001
        """返回增强版 Swagger UI 页面."""

        return HTMLResponse(page_html)

    # 移除 FastAPI 构造时注册的默认 /docs 路由，再注册自定义页面路由。
    # Starlette 按注册顺序匹配路由，必须移除原路由而非追加。
    app.router.routes = [
        route
        for route in app.router.routes
        if not (isinstance(route, Route) and route.path == docs_url)
    ]
    app.add_route(docs_url, custom_swagger_ui, include_in_schema=False)
