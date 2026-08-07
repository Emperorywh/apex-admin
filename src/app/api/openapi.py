"""OpenAPI 文档生成与配置（SPEC §9.6、§34.1）。

提供自定义 OpenAPI schema 生成，确保：
- 操作按模块标签分组（SPEC §9.6）
- 每个操作有摘要和必要说明（SPEC §9.6）
- Operation ID 全局唯一且稳定（SPEC §9.6）
- Bearer 认证方案集成到 OpenAPI 安全方案（SPEC §9.6、§12.1）
- 生产环境可禁用交互式文档（SPEC §9.6）
- 文档不暴露内部密钥、数据库信息和堆栈（SPEC §9.6）

OpenAPI 快照是活的契约——随端点添加而变化，但每次变化必须是有意且经过评审的
（SPEC §34.1）。CI 通过快照比较检测无意变更，开发者需显式更新快照以纳入新增端点。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.config.settings import AppEnv, Settings

_logger = logging.getLogger("app.api.openapi")

# Bearer 认证安全方案名称（SPEC §12.1：G2 使用不透明 Bearer Access Token）
SECURITY_SCHEME_NAME = "BearerAuth"

# OpenAPI schema 在 app 实例上的缓存属性名，避免重复生成
_OPENAPI_SCHEMA_ATTR = "_apex_openapi_schema"

# OpenAPI path item 中可包含的 HTTP 方法集合
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})


def custom_openapi(app: FastAPI) -> dict[str, Any]:
    """自定义 OpenAPI schema 生成（SPEC §9.6）。

    在 FastAPI 默认 schema 基础上：
    - 集成 Bearer 认证安全方案（SPEC §9.6、§12.1）
    - 验证 Operation ID 唯一性（SPEC §9.6）

    生成的 schema 缓存在 app 实例上，避免重复生成。

    Args:
        app: FastAPI 应用实例

    Returns:
        OpenAPI 3.1 schema 字典

    Raises:
        ValueError: 存在重复 Operation ID 时（SPEC §9.6 唯一性要求）
    """
    cached: dict[str, Any] | None = getattr(app, _OPENAPI_SCHEMA_ATTR, None)
    if cached is not None:
        return cached

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        summary=app.summary,
        routes=app.routes,
    )

    # 集成 Bearer 认证安全方案（SPEC §9.6、§12.1）
    # G2 使用不透明 Bearer Access Token（SPEC §12.1），
    # 此处声明安全方案供受保护端点引用。
    components = openapi_schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes[SECURITY_SCHEME_NAME] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "Bearer Token",
        "description": "使用 Bearer Access Token 进行认证（SPEC §12.1）",
    }

    # 验证 Operation ID 唯一性（SPEC §9.6）
    _assert_unique_operation_ids(openapi_schema)

    setattr(app, _OPENAPI_SCHEMA_ATTR, openapi_schema)
    return openapi_schema


def get_documentation_urls(settings: Settings) -> tuple[str | None, str | None, str | None]:
    """根据运行环境返回文档端点 URL 配置（SPEC §9.6）。

    生产环境禁用全部文档端点（Swagger UI、ReDoc 和 OpenAPI JSON），
    防止暴露接口结构（SPEC §9.6）。此函数在 FastAPI 初始化前调用，
    确保 None 配置从构造阶段生效——若在构造后修改属性，已注册的路由
    不会被移除。

    Args:
        settings: 已校验的部署配置

    Returns:
        ``(docs_url, redoc_url, openapi_url)`` 三元组；
        生产环境全部为 None 禁用端点，其他环境返回 FastAPI 默认值。
    """
    if settings.app_env == AppEnv.PRODUCTION:
        _logger.info(
            "生产环境已禁用交互式文档端点",
            extra={"app_env": settings.app_env.value},
        )
        return None, None, None
    # 非生产环境使用 FastAPI 默认文档 URL
    return "/docs", "/redoc", "/openapi.json"


def configure_documentation(app: FastAPI) -> None:
    """配置应用的 OpenAPI schema 生成（SPEC §9.6）。

    设置自定义 OpenAPI schema 生成函数，确保 Operation ID 唯一性校验和
    Bearer 认证方案集成在 schema 生成时执行。

    文档端点 URL 的启用/禁用通过 :func:`get_documentation_urls` 在
    FastAPI 构造阶段处理。

    Args:
        app: FastAPI 应用实例
    """

    def _custom_openapi() -> dict[str, Any]:
        return custom_openapi(app)

    app.openapi = _custom_openapi  # type: ignore[method-assign]


def _assert_unique_operation_ids(schema: dict[str, Any]) -> None:
    """验证所有 Operation ID 唯一（SPEC §9.6）。

    Operation ID 必须全局唯一且稳定。此函数在 schema 生成时执行校验，
    确保任何路由重复都会在应用启动或测试阶段被立即发现。

    Args:
        schema: OpenAPI schema 字典

    Raises:
        ValueError: 存在重复 Operation ID 时
    """
    seen: set[str] = set()
    duplicates: list[str] = []

    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if op_id is None:
                continue
            if op_id in seen:
                duplicates.append(op_id)
            seen.add(op_id)

    if duplicates:
        unique_dupes = sorted(set(duplicates))
        raise ValueError(
            f"OpenAPI Operation ID 重复（违反 SPEC §9.6 唯一性要求）: {', '.join(unique_dupes)}"
        )
