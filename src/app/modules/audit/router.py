"""审计查询 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 9.4 / 18.3）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case"。

路由组织（SPEC 9.1: 按模块分组）:
  操作审计查询 — ``/audit/logs`` 前缀:
    GET  /audit/logs           分页查询审计日志（含筛选）
    GET  /audit/logs/export    流式导出审计日志（CSV）
    GET  /audit/logs/{id}      查询审计日志详情

  登录日志查询 — ``/audit/login-logs`` 前缀:
    GET  /audit/login-logs           分页查询登录日志（含筛选）
    GET  /audit/login-logs/export    流式导出登录日志（CSV）
    GET  /audit/login-logs/{id}      查询登录日志详情

注意: ``/export`` 路由必须在 ``/{id}`` 路由之前注册，
否则 ``export`` 会被路径参数 ``{id}`` 匹配。

SPEC 18.3: 审计日志查询本身受到权限控制。
SPEC 18.3: 审计日志导出由审计模块以流式文件下载自行实现，
不依赖 22.1 的通用导出扩展；导出属于受控操作并记录新的审计事件。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Path, Query, Request
from starlette.responses import StreamingResponse

from app.application.context import UseCaseContext
from app.core.api.pagination import PageParams, total_pages
from app.modules.audit.query_port import AuditLogFilters, LoginLogFilters
from app.modules.audit.query_use_case import AuditQueryUseCase
from app.modules.audit.schemas import (
    AuditLogPageResponse,
    AuditLogResponse,
    LoginLogPageResponse,
    LoginLogResponse,
)
from app.modules.auth.permission import require_permission

router = APIRouter(tags=["audit"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_audit_query_use_case(request: Request) -> AuditQueryUseCase:
    """构造 ``AuditQueryUseCase`` — 组合根装配（SPEC 5.2）."""

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter

    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计写入 Port — SPEC 5.7 / 5.2."""

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    return AuditQueryUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=audit_factory,
    )


UseCaseDep = Annotated[AuditQueryUseCase, Depends(get_audit_query_use_case)]

AuditReadCtx = Annotated[
    UseCaseContext,
    Depends(require_permission("audit:log:read")),
]
AuditExportCtx = Annotated[
    UseCaseContext,
    Depends(require_permission("audit:log:export")),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 操作审计查询 — /audit/logs
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/audit/logs",
    response_model=AuditLogPageResponse,
    summary="分页查询审计日志",
    operation_id="list_audit_logs",
)
async def list_audit_logs(
    ctx: AuditReadCtx,
    use_case: UseCaseDep,
    params: Annotated[PageParams, Depends()],
    actor_id: Annotated[
        str | None,
        Query(
            alias="actorId",
            description="操作者标识筛选",
        ),
    ] = None,
    module: Annotated[str | None, Query(description="操作模块筛选")] = None,
    action: Annotated[str | None, Query(description="审计动作筛选")] = None,
    resource_type: Annotated[
        str | None,
        Query(
            alias="resourceType",
            description="资源类型筛选",
        ),
    ] = None,
    resource_id: Annotated[
        str | None,
        Query(
            alias="resourceId",
            description="资源标识筛选",
        ),
    ] = None,
    result: Annotated[str | None, Query(description="操作结果筛选")] = None,
    start_time: Annotated[
        datetime | None,
        Query(
            alias="startTime",
            description="发生时间下界（含），ISO 8601",
        ),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Query(
            alias="endTime",
            description="发生时间上界（含），ISO 8601",
        ),
    ] = None,
) -> AuditLogPageResponse:
    """分页查询审计日志 — SPEC 18.3.

    支持按操作者/模块/动作/资源/结果/时间范围筛选（SPEC 18.3）。
    审计查询本身受到权限控制（SPEC 18.3）。
    """

    filters = AuditLogFilters(
        actor_id=actor_id,
        module=module,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        start_time=start_time,
        end_time=end_time,
    )
    items, total = await use_case.query_audit_logs(
        ctx,
        filters,
        offset=params.offset,
        limit=params.page_size,
    )
    return AuditLogPageResponse(
        items=[AuditLogResponse.model_validate(item) for item in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=total_pages(total, params.page_size),
    )


@router.get(
    "/audit/logs/export",
    summary="导出审计日志（流式 CSV）",
    operation_id="export_audit_logs",
)
async def export_audit_logs(
    ctx: AuditExportCtx,
    use_case: UseCaseDep,
    actor_id: Annotated[
        str | None,
        Query(
            alias="actorId",
            description="操作者标识筛选",
        ),
    ] = None,
    module: Annotated[str | None, Query(description="操作模块筛选")] = None,
    action: Annotated[str | None, Query(description="审计动作筛选")] = None,
    resource_type: Annotated[
        str | None,
        Query(
            alias="resourceType",
            description="资源类型筛选",
        ),
    ] = None,
    resource_id: Annotated[
        str | None,
        Query(
            alias="resourceId",
            description="资源标识筛选",
        ),
    ] = None,
    result: Annotated[str | None, Query(description="操作结果筛选")] = None,
    start_time: Annotated[
        datetime | None,
        Query(
            alias="startTime",
            description="发生时间下界（含），ISO 8601",
        ),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Query(
            alias="endTime",
            description="发生时间上界（含），ISO 8601",
        ),
    ] = None,
) -> StreamingResponse:
    """流式导出审计日志为 CSV — SPEC 18.3.

    SPEC 18.3: 审计日志导出由审计模块以流式文件下载自行实现，
    不依赖 22.1 的通用导出扩展；导出属于受控操作并记录新的审计事件。
    """

    filters = AuditLogFilters(
        actor_id=actor_id,
        module=module,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        start_time=start_time,
        end_time=end_time,
    )

    generator = use_case.export_audit_logs(ctx, filters)
    return StreamingResponse(
        generator,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@router.get(
    "/audit/logs/{logId}",
    response_model=AuditLogResponse,
    summary="查询审计日志详情",
    operation_id="get_audit_log",
)
async def get_audit_log(
    ctx: AuditReadCtx,
    use_case: UseCaseDep,
    log_id: Annotated[UUID, Path(alias="logId", description="审计日志 ID")],
) -> AuditLogResponse:
    """查询单条审计日志详情 — SPEC 18.3."""

    result = await use_case.get_audit_log(ctx, log_id)
    return AuditLogResponse.model_validate(result)


# ═══════════════════════════════════════════════════════════════════════════════
# 登录日志查询 — /audit/login-logs
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/audit/login-logs",
    response_model=LoginLogPageResponse,
    summary="分页查询登录日志",
    operation_id="list_login_logs",
)
async def list_login_logs(
    ctx: AuditReadCtx,
    use_case: UseCaseDep,
    params: Annotated[PageParams, Depends()],
    user_id: Annotated[
        str | None,
        Query(alias="userId", description="用户标识筛选"),
    ] = None,
    username: Annotated[str | None, Query(description="登录账号筛选")] = None,
    ip_address: Annotated[
        str | None,
        Query(
            alias="ipAddress",
            description="客户端 IP 筛选",
        ),
    ] = None,
    result: Annotated[str | None, Query(description="登录结果筛选")] = None,
    start_time: Annotated[
        datetime | None,
        Query(
            alias="startTime",
            description="发生时间下界（含），ISO 8601",
        ),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Query(
            alias="endTime",
            description="发生时间上界（含），ISO 8601",
        ),
    ] = None,
) -> LoginLogPageResponse:
    """分页查询登录日志 — SPEC 18.1 / 18.3.

    支持按用户/IP/结果/时间范围筛选（SPEC 18.1）。
    """

    filters = LoginLogFilters(
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        result=result,
        start_time=start_time,
        end_time=end_time,
    )
    items, total = await use_case.query_login_logs(
        ctx,
        filters,
        offset=params.offset,
        limit=params.page_size,
    )
    return LoginLogPageResponse(
        items=[LoginLogResponse.model_validate(item) for item in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=total_pages(total, params.page_size),
    )


@router.get(
    "/audit/login-logs/export",
    summary="导出登录日志（流式 CSV）",
    operation_id="export_login_logs",
)
async def export_login_logs(
    ctx: AuditExportCtx,
    use_case: UseCaseDep,
    user_id: Annotated[
        str | None,
        Query(alias="userId", description="用户标识筛选"),
    ] = None,
    username: Annotated[str | None, Query(description="登录账号筛选")] = None,
    ip_address: Annotated[
        str | None,
        Query(
            alias="ipAddress",
            description="客户端 IP 筛选",
        ),
    ] = None,
    result: Annotated[str | None, Query(description="登录结果筛选")] = None,
    start_time: Annotated[
        datetime | None,
        Query(
            alias="startTime",
            description="发生时间下界（含），ISO 8601",
        ),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Query(
            alias="endTime",
            description="发生时间上界（含），ISO 8601",
        ),
    ] = None,
) -> StreamingResponse:
    """流式导出登录日志为 CSV — SPEC 18.3."""

    filters = LoginLogFilters(
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        result=result,
        start_time=start_time,
        end_time=end_time,
    )

    generator = use_case.export_login_logs(ctx, filters)
    return StreamingResponse(
        generator,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=login_logs.csv"},
    )


@router.get(
    "/audit/login-logs/{logId}",
    response_model=LoginLogResponse,
    summary="查询登录日志详情",
    operation_id="get_login_log",
)
async def get_login_log(
    ctx: AuditReadCtx,
    use_case: UseCaseDep,
    log_id: Annotated[UUID, Path(alias="logId", description="登录日志 ID")],
) -> LoginLogResponse:
    """查询单条登录日志详情 — SPEC 18.3."""

    result = await use_case.get_login_log(ctx, log_id)
    return LoginLogResponse.model_validate(result)
