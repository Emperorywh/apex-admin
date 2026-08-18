"""自定义 Swagger UI 文档页测试 — SPEC 9.6.

覆盖:
  - /docs 返回自定义增强页面（搜索 / 复制文档 / 全局参数）。
  - 页面模板占位符已正确渲染（标题、OpenAPI 地址、CDN）。
  - 文档端点关闭时自定义路由不注册（生产关闭行为不受影响）。
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


def _production_settings(enable_docs: bool) -> Settings:
    """构造生产环境配置（复用 test_openapi.py 的必填密钥占位值）。"""

    return Settings(
        ENVIRONMENT=Environment.PRODUCTION,
        ENABLE_API_DOCS=enable_docs,
        DATABASE_URL="postgresql+psycopg://apex@127.0.0.1:55432/postgres",
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
        SYSCONFIG_ENCRYPTION_KEY="T44-h5wE4-HJ69EZjyDir3a_DNQFAT5DMW8De0tXijU=",
        TRUSTED_HOSTS="testserver,admin.example.com",
        METRICS_TOKEN="prod-metrics-secret-token",
    )


# ── 自定义页面内容 ────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.api
def test_docs_serves_custom_page() -> None:
    """/docs 返回自定义增强页面而非 FastAPI 默认页面。"""

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    # 自定义页面标记
    assert "apex-docs-enhancements" in body
    # 不是 FastAPI 默认页面（默认页含 swagger-ui-init 脚本）
    assert "swagger-ui-init" not in body


@pytest.mark.g1
@pytest.mark.api
def test_docs_page_contains_enhancement_features() -> None:
    """自定义页面包含三项增强功能的实现脚本."""

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/docs")

    body = response.text
    # 功能 1: 接口搜索 — 启用过滤栏
    assert "filter: true" in body
    # 功能 2: 单个 API 文档复制
    assert "复制文档" in body
    assert "buildApiMarkdown" in body
    # 注入逻辑依赖的选择器（swagger-ui-dist 5.x 实际类名）
    assert "opblock-summary-method" in body
    assert 'data-path' in body
    # 功能 3: 全局参数设置
    assert "全局参数设置" in body
    assert "apex.docs.globalParams" in body


@pytest.mark.g1
@pytest.mark.api
def test_docs_page_template_placeholders_rendered() -> None:
    """模板占位符全部渲染 — 不残留 {{ ... }}，注入标题与 OpenAPI 地址."""

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/docs")

    body = response.text
    assert "{{" not in body
    assert "}}" not in body
    # 应用名称与 OpenAPI 地址注入
    assert app.state.settings.APP_NAME in body
    assert "/openapi.json" in body


@pytest.mark.g1
@pytest.mark.unit
def test_docs_page_uses_configured_cdn() -> None:
    """自定义页面按配置加载 swagger-ui-dist 静态资源."""

    settings = Settings(
        SWAGGER_CDN_BASE="https://example.internal-cdn.test/swagger-ui-dist@5",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/docs")

    assert (
        "https://example.internal-cdn.test/swagger-ui-dist@5/swagger-ui.css"
        in response.text
    )


# ── 文档端点关闭时自定义路由不注册 ────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_production_disables_custom_docs_page() -> None:
    """生产环境关闭文档时，自定义 /docs 路由同样返回 404（SPEC 9.6）。"""

    app = create_app(_production_settings(enable_docs=False))
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404


@pytest.mark.g1
@pytest.mark.unit
def test_production_explicitly_enabled_serves_custom_page() -> None:
    """生产环境显式开启文档时，/docs 返回自定义增强页面。"""

    app = create_app(_production_settings(enable_docs=True))
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert "apex-docs-enhancements" in response.text
