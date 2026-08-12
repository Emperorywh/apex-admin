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

from typing import TYPE_CHECKING, Any

from app.core.config import Environment, Settings

if TYPE_CHECKING:
    from fastapi import FastAPI

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
    {
        "name": "identity",
        "description": (
            "用户管理 — 用户生命周期（创建/详情/分页/更新/启用/禁用/"
            "重置密码）、自助资料与改密、删除策略与审计（SPEC 11.1 / 11.2 / 11.3）。"
        ),
    },
    {
        "name": "auth",
        "description": (
            "认证与会话管理 — 登录、退出、会话查看、Refresh Token 轮换"
            "（SPEC 12.1 / 12.2 / 12.3 / 12.4 / 18.1）。"
        ),
    },
    {
        "name": "rbac",
        "description": (
            "RBAC 角色与权限点 — 角色 CRUD、启用/禁用、权限分配、"
            "角色成员查询、用户角色分配/移除、内置角色保护（SPEC 13.1 / 13.2）。"
        ),
    },
]


def resolve_docs_urls(settings: Settings) -> dict[str, str | None]:
    """解析文档端点路径 — SPEC 9.6.

    生产环境（或 ``ENABLE_API_DOCS=False``）时关闭文档端点，
    非生产环境默认开启。

    参数:
        settings: 部署配置实例。

    返回:
        包含 ``docs_url`` / ``redoc_url`` / ``openapi_url`` 三项的字典；
        文档关闭时各项为 None。
    """

    if settings.ENVIRONMENT == Environment.PRODUCTION and not _docs_explicitly_enabled(
        settings,
    ):
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}

    if not settings.ENABLE_API_DOCS:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}

    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


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

    return {"openapi_tags": OPENAPI_TAGS, **resolve_docs_urls(settings)}


def _docs_explicitly_enabled(settings: Settings) -> bool:
    """检查生产环境是否显式启用了 API 文档。

    生产环境默认关闭文档，但 ``APEX_ENABLE_API_DOCS=true`` 可显式开启。
    """

    return settings.ENABLE_API_DOCS


# ── OpenAPI 安全方案（SPEC 9.6 / 12.1 / 12.3）─────────────────────────────
#
# 注册 HTTP Bearer 认证方案，使 Swagger UI 顶部出现 Authorize 按钮。
# 登录后将 access_token 填入，Swagger 在后续请求中自动携带
# ``Authorization: Bearer <token>``。
#
# 认证运行时逻辑仍由 app/modules/auth/dependencies.py 手动从 header
# 提取不透明 Access Token（SPEC 12.1），此处仅声明文档层面的安全方案，
# 不改动任何认证依赖。

#: BearerAuth 安全方案 — 不透明 Access Token（非 JWT）。
_BEARER_SECURITY_SCHEME: dict[str, Any] = {
    "type": "http",
    "scheme": "bearer",
    "bearerFormat": "Opaque",
    "description": (
        "粘贴登录接口返回的 access_token（无需手写 Bearer 前缀）。"
        "认证依赖从 Authorization: Bearer <token> 提取不透明 Access Token"
        "（SPEC 12.1）。"
    ),
}

#: 全局默认安全要求 — 受保护端点均需 Bearer Token。
_DEFAULT_SECURITY: list[dict[str, list[str]]] = [{"BearerAuth": []}]

#: 公开端点 tag — 这些端点豁免全局 Bearer 要求。
_PUBLIC_TAGS: frozenset[str] = frozenset({"health", "meta", "metrics"})

#: auth 模块的公开路径后缀 — 登录与刷新不需要 Access Token。
_PUBLIC_AUTH_PATH_SUFFIXES: frozenset[str] = frozenset({"/auth/login", "/auth/refresh"})

#: 需要遍历 operation 的 HTTP 方法集合。
_HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"},
)


def _is_public_operation(path: str, operation: dict[str, Any]) -> bool:
    """判断一个 operation 是否为公开端点（豁免 Bearer 认证）.

    判定规则:
      - tag 属于公开 tag（health / meta / metrics）。
      - 路径以登录或刷新后缀结尾（auth 模块的公开入口）。
    """

    tags = operation.get("tags", [])
    if any(tag in _PUBLIC_TAGS for tag in tags):
        return True
    normalized = path.lower()
    return any(normalized.endswith(suffix) for suffix in _PUBLIC_AUTH_PATH_SUFFIXES)


def _mark_public_endpoints(schema: dict[str, Any]) -> None:
    """为公开端点标注 ``security: []`` 以豁免全局 Bearer 要求."""

    paths = schema.get("paths", {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            if _is_public_operation(path, operation):
                operation["security"] = []


def apply_openapi_security(app: FastAPI) -> None:
    """为应用注册自定义 openapi 方法，注入 Bearer 安全方案 — SPEC 9.6 / 12.3.

    覆盖 ``app.openapi``，在 FastAPI 自动生成的 OpenAPI schema 基础上注入:
      - ``components.securitySchemes.BearerAuth``（HTTP Bearer 方案）。
      - 全局 ``security`` 默认要求（所有端点默认需要 Bearer Token）。

    公开端点（health / meta / metrics、auth 登录与刷新）通过
    ``_mark_public_endpoints`` 标注 ``security: []`` 豁免。

    Swagger UI 据此在页面顶部渲染 Authorize 🔓 按钮：登录后填入
    access_token 即可全局授权，Swagger 自动在受保护请求中携带
    ``Authorization: Bearer <token>``。

    参数:
        app: 待配置的 FastAPI 应用实例。
    """

    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        # 由原始方法生成 schema（内部会缓存到 app.openapi_schema）
        schema = original_openapi()
        # 注入 Bearer 安全方案
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["BearerAuth"] = _BEARER_SECURITY_SCHEME
        # 全局默认安全要求
        schema["security"] = _DEFAULT_SECURITY
        # 公开端点豁免
        _mark_public_endpoints(schema)
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
