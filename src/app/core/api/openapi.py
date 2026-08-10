"""OpenAPI 文档定制 — SPEC 9.6.

SPEC 9.6 约定:
  - 接口按模块分组（tags_metadata）。
  - 每个 Operation ID 全局唯一且稳定。
  - 生产环境可以关闭文档或限制访问。
  - 文档不暴露内部密钥、数据库信息和堆栈详情。

本模块提供 OpenAPI tags 元数据和 ``build_openapi_kwargs`` 辅助函数，
供 ``create_app`` 在构造 FastAPI 实例时统一配置文档行为。
"""

from __future__ import annotations

from typing import Any

from app.core.config import Environment, Settings

# ── OpenAPI Tags 元数据 ──────────────────────────────────────────────────
#
# SPEC 9.6: "接口按模块分组"。
# 每个业务模块在注册路由时使用对应的 tag，此元数据为每个 tag
# 提供中文描述。业务模块新增时在此列表追加对应 tag 条目。

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "health",
        "description": "健康检查端点 — 存活检查与就绪检查（SPEC 6.2）",
    },
    {
        "name": "meta",
        "description": "应用元数据端点 — 应用名称、版本与运行环境（SPEC 6.1）",
    },
    {
        "name": "example",
        "description": (
            "最小示例模块 — 端到端演示 Router/Use Case/Port/Adapter/"
            "迁移/权限/错误码/事件完整接入（SPEC 30.2 / 34.1）。"
            "派生项目可整体删除。"
        ),
    },
]


def build_openapi_kwargs(settings: Settings) -> dict[str, Any]:
    """根据部署配置构建 FastAPI OpenAPI 参数 — SPEC 9.6.

    生产环境（或 ``ENABLE_API_DOCS=False``）时关闭文档端点：
    - ``docs_url=None`` → ``/docs`` (Swagger UI) 返回 404
    - ``redoc_url=None`` → ``/redoc`` (ReDoc) 返回 404
    - ``openapi_url=None`` → ``/openapi.json`` 返回 404

    非生产环境默认开启文档。

    参数:
        settings: 部署配置实例。

    返回:
        传递给 ``FastAPI(...)`` 构造函数的 OpenAPI 相关参数字典。
    """

    if settings.ENVIRONMENT == Environment.PRODUCTION and not _docs_explicitly_enabled(
        settings,
    ):
        return {
            "openapi_tags": OPENAPI_TAGS,
            "docs_url": None,
            "redoc_url": None,
            "openapi_url": None,
        }

    if not settings.ENABLE_API_DOCS:
        return {
            "openapi_tags": OPENAPI_TAGS,
            "docs_url": None,
            "redoc_url": None,
            "openapi_url": None,
        }

    return {
        "openapi_tags": OPENAPI_TAGS,
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }


def _docs_explicitly_enabled(settings: Settings) -> bool:
    """检查生产环境是否显式启用了 API 文档。

    生产环境默认关闭文档，但 ``APEX_ENABLE_API_DOCS=true`` 可显式开启。
    """

    return settings.ENABLE_API_DOCS
