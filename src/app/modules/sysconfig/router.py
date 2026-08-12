"""系统配置 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 16.1）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case"。

路由组织（SPEC 9.1: 按模块分组）:
  配置管理 — ``/configs`` 前缀:
    POST   /configs                创建配置项
    GET    /configs                查询配置项列表（支持 group 过滤）
    GET    /configs/groups         查询配置分组列表
    GET    /configs/{configId}    查询配置项详情
    PUT    /configs/{configId}    更新配置项
    POST   /configs/{configId}/enable   启用配置项
    POST   /configs/{configId}/disable  禁用配置项
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.application.context import UseCaseContext
from app.modules.auth.permission import require_permission
from app.modules.sysconfig.schemas import (
    ConfigCreateRequest,
    ConfigGroupResponse,
    ConfigResponse,
    ConfigUpdateRequest,
)
from app.modules.sysconfig.use_case import ConfigUseCase

router = APIRouter(tags=["sysconfig"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_config_use_case(request: Request) -> ConfigUseCase:
    """构造 ``ConfigUseCase`` — 组合根装配（SPEC 5.2）."""

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter
    from app.modules.sysconfig.crypto import ConfigEncryptionService

    engine = request.app.state.db_engine
    settings = request.app.state.settings

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    # SPEC 23.2: 敏感配置加密密钥来自部署配置
    encryption = ConfigEncryptionService(
        current_key=settings.SYSCONFIG_ENCRYPTION_KEY.get_secret_value(),
        previous_key=(
            settings.SYSCONFIG_ENCRYPTION_KEY_PREVIOUS.get_secret_value()
            if settings.SYSCONFIG_ENCRYPTION_KEY_PREVIOUS is not None
            else None
        ),
    )

    return ConfigUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=audit_factory,
        encryption_service=encryption,
    )


UseCaseDep = Annotated[ConfigUseCase, Depends(get_config_use_case)]

ConfigReadCtx = Annotated[
    UseCaseContext, Depends(require_permission("sysconfig:config:read"))
]
ConfigWriteCtx = Annotated[
    UseCaseContext, Depends(require_permission("sysconfig:config:write"))
]


# ═══════════════════════════════════════════════════════════════════════════════
# 配置项管理 — /configs 前缀
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/configs",
    response_model=ConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建配置项",
    operation_id="create_config",
)
async def create_config(
    response: Response,
    request_body: ConfigCreateRequest,
    ctx: ConfigWriteCtx,
    use_case: UseCaseDep,
) -> ConfigResponse:
    """创建配置项 — HTTP 201 + Location（SPEC 9.3 / 16.1）.

    配置键在分组内唯一。配置值按声明类型校验。
    敏感配置加密存储且 API 不回显明文（SPEC 16.1）。
    """

    result = await use_case.create_config(ctx, request_body)
    response.headers["Location"] = f"/api/v1/configs/{result['id']}"
    return ConfigResponse.model_validate(result)


@router.get(
    "/configs",
    response_model=list[ConfigResponse],
    summary="查询配置项列表",
    operation_id="list_configs",
)
async def list_configs(
    ctx: ConfigReadCtx,
    use_case: UseCaseDep,
    group: Annotated[
        str | None,
        Query(description="按分组过滤"),
    ] = None,
    include_disabled: Annotated[
        bool,
        Query(
            alias="includeDisabled",
            description="是否包含禁用状态的配置项（默认 true）",
        ),
    ] = True,
) -> list[ConfigResponse]:
    """查询配置项列表 — SPEC 16.1 按分组管理.

    敏感配置值在响应中掩码（SPEC 16.1: 默认不回显）。
    """

    results = await use_case.list_configs(
        ctx,
        group=group,
        include_disabled=include_disabled,
    )
    return [ConfigResponse.model_validate(r) for r in results]


@router.get(
    "/configs/groups",
    response_model=list[ConfigGroupResponse],
    summary="查询配置分组列表",
    operation_id="list_config_groups",
)
async def list_config_groups(
    ctx: ConfigReadCtx,
    use_case: UseCaseDep,
) -> list[ConfigGroupResponse]:
    """查询配置分组列表 — SPEC 16.1 按分组管理."""

    results = await use_case.list_groups(ctx)
    return [ConfigGroupResponse.model_validate(r) for r in results]


@router.get(
    "/configs/{configId}",
    response_model=ConfigResponse,
    summary="查询配置项详情",
    operation_id="get_config",
)
async def get_config(
    ctx: ConfigReadCtx,
    use_case: UseCaseDep,
    config_id: Annotated[UUID, Path(alias="configId", description="配置项 ID")],
) -> ConfigResponse:
    """查询配置项详情 — SPEC 16.1.

    敏感配置值在响应中掩码（SPEC 16.1: 默认不回显）。
    不存在返回 404。
    """

    result = await use_case.get_config(ctx, config_id)
    return ConfigResponse.model_validate(result)


@router.put(
    "/configs/{configId}",
    response_model=ConfigResponse,
    summary="更新配置项",
    operation_id="update_config",
)
async def update_config(
    request_body: ConfigUpdateRequest,
    ctx: ConfigWriteCtx,
    use_case: UseCaseDep,
    config_id: Annotated[UUID, Path(alias="configId", description="配置项 ID")],
) -> ConfigResponse:
    """更新配置项 — SPEC 16.1.

    核心安全配置不可通过此端点更新（SPEC 16.1: 不可被普通后台覆盖）。
    配置值按声明类型校验。
    """

    result = await use_case.update_config(ctx, config_id, request_body)
    return ConfigResponse.model_validate(result)


@router.post(
    "/configs/{configId}/enable",
    response_model=ConfigResponse,
    summary="启用配置项",
    operation_id="enable_config",
)
async def enable_config(
    ctx: ConfigWriteCtx,
    use_case: UseCaseDep,
    config_id: Annotated[UUID, Path(alias="configId", description="配置项 ID")],
) -> ConfigResponse:
    """启用配置项 — SPEC 16.1.

    核心安全配置不可通过此端点操作（SPEC 16.1）。
    """

    result = await use_case.enable_config(ctx, config_id)
    return ConfigResponse.model_validate(result)


@router.post(
    "/configs/{configId}/disable",
    response_model=ConfigResponse,
    summary="禁用配置项",
    operation_id="disable_config",
)
async def disable_config(
    ctx: ConfigWriteCtx,
    use_case: UseCaseDep,
    config_id: Annotated[UUID, Path(alias="configId", description="配置项 ID")],
) -> ConfigResponse:
    """禁用配置项 — SPEC 16.1.

    核心安全配置不可通过此端点操作（SPEC 16.1）。
    """

    result = await use_case.disable_config(ctx, config_id)
    return ConfigResponse.model_validate(result)
