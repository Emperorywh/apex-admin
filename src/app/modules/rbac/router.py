"""RBAC 模块 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 13.2）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository 或提交接口"。

Router 通过 FastAPI 依赖注入获得 ``RbacUseCase``。

路由组织（SPEC 9.1: 按模块分组）:
  角色管理 — ``/roles`` 前缀:
    POST   /roles                          创建角色
    GET    /roles                          分页查询
    GET    /roles/{roleId}                查询详情（含权限和成员数）
    PUT    /roles/{roleId}                更新角色
    POST   /roles/{roleId}/enable         启用角色
    POST   /roles/{roleId}/disable        禁用角色
    DELETE /roles/{roleId}                删除角色（内置角色保护）
    PUT    /roles/{roleId}/permissions    分配权限点
    GET    /roles/{roleId}/members        查询角色成员

  用户角色 — ``/users`` 前缀:
    GET    /users/{userId}/roles          查询用户角色
    PUT    /users/{userId}/roles          为用户分配角色（全量替换）
    DELETE /users/{userId}/roles/{roleId} 移除用户角色
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.application.context import UseCaseContext
from app.core.api.pagination import (
    PageParams,
    PageResponse,
    SortField,
    sort_dependency,
)
from app.modules.auth.permission import require_permission
from app.modules.rbac.models import RoleStatus
from app.modules.rbac.schemas import (
    AssignPermissionsRequest,
    AssignUserRolesRequest,
    RoleCreateRequest,
    RoleDetailResponse,
    RoleMemberResponse,
    RoleResponse,
    RoleUpdateRequest,
)
from app.modules.rbac.use_case import RbacUseCase

# ── 排序白名单 — SPEC 9.4 ──────────────────────────────────────────────────

_ROLE_SORT_FIELDS = frozenset({"code", "display_name", "created_at", "updated_at"})

router = APIRouter(tags=["rbac"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_rbac_use_case(request: Request) -> RbacUseCase:
    """构造 ``RbacUseCase`` — 组合根装配（SPEC 5.2）."""

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter
    from app.modules.identity import adapter as _identity_adapter

    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造用户认证信息 Port — SPEC 5.2 跨模块."""

        return _identity_adapter.SqlAlchemyUserAuthAdapter(session)

    def user_rbac_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造用户 RBAC Port — SPEC 5.2 / 13.3 二次校验."""

        from app.modules.rbac.adapter import SqlAlchemyUserRbacAdapter

        return SqlAlchemyUserRbacAdapter(session)

    return RbacUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=audit_factory,
        user_auth_port_factory=user_auth_port_factory,
        user_rbac_port_factory=user_rbac_port_factory,
    )


UseCaseDep = Annotated[RbacUseCase, Depends(get_rbac_use_case)]

# SPEC 13.3 / 23.5: 管理端通过权限依赖保护。
RoleReadCtx = Annotated[UseCaseContext, Depends(require_permission("rbac:role:read"))]
RoleWriteCtx = Annotated[UseCaseContext, Depends(require_permission("rbac:role:write"))]
AssignmentWriteCtx = Annotated[
    UseCaseContext, Depends(require_permission("rbac:assignment:write"))
]


# ═══════════════════════════════════════════════════════════════════════════════
# 角色管理 — /roles 前缀
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建角色",
    operation_id="create_role",
)
async def create_role(
    response: Response,
    request_body: RoleCreateRequest,
    ctx: RoleWriteCtx,
    use_case: UseCaseDep,
) -> RoleResponse:
    """创建角色 — HTTP 201 + Location（SPEC 9.3 / 13.2）.

    角色编码冲突返回 409（``RBAC.ROLE_ALREADY_EXISTS``）。
    """

    result = await use_case.create_role(ctx, request_body)
    response.headers["Location"] = f"/api/v1/roles/{result.id}"
    return result


@router.get(
    "/roles",
    response_model=PageResponse[RoleResponse],
    summary="分页查询角色列表",
    operation_id="list_roles",
)
async def list_roles(
    ctx: RoleReadCtx,
    use_case: UseCaseDep,
    params: Annotated[PageParams, Depends()],
    sort: Annotated[
        list[SortField],
        Depends(sort_dependency(_ROLE_SORT_FIELDS)),
    ],
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="按角色状态筛选（active / disabled）",
        ),
    ] = None,
) -> dict[str, object]:
    """分页查询角色列表 — SPEC 9.4 / 13.2."""

    parsed_status: RoleStatus | None = None
    if status_filter is not None:
        parsed_status = RoleStatus(status_filter)

    return await use_case.list_roles(
        ctx,
        page=params.page,
        page_size=params.page_size,
        sort_fields=sort,
        status_filter=parsed_status,
    )


@router.get(
    "/roles/{roleId}",
    response_model=RoleDetailResponse,
    summary="查询角色详情",
    operation_id="get_role_detail",
)
async def get_role_detail(
    ctx: RoleReadCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> RoleDetailResponse:
    """查询角色详情（含权限编码列表和成员数量）— SPEC 13.2.

    不存在返回 404（``RBAC.ROLE_NOT_FOUND``）。
    """

    return await use_case.get_role_detail(ctx, role_id)


@router.put(
    "/roles/{roleId}",
    response_model=RoleResponse,
    summary="更新角色",
    operation_id="update_role",
)
async def update_role(
    request_body: RoleUpdateRequest,
    ctx: RoleWriteCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> RoleResponse:
    """更新角色 — SPEC 13.2."""

    return await use_case.update_role(ctx, role_id, request_body)


@router.post(
    "/roles/{roleId}/enable",
    response_model=RoleResponse,
    summary="启用角色",
    operation_id="enable_role",
)
async def enable_role(
    ctx: RoleWriteCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> RoleResponse:
    """启用角色 — SPEC 13.2.

    内置角色不可启用/禁用（``RBAC.BUILTIN_ROLE_PROTECTED``）。
    """

    return await use_case.enable_role(ctx, role_id)


@router.post(
    "/roles/{roleId}/disable",
    response_model=RoleResponse,
    summary="禁用角色",
    operation_id="disable_role",
)
async def disable_role(
    ctx: RoleWriteCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> RoleResponse:
    """禁用角色 — SPEC 13.2.

    SPEC 13.1: 被禁用角色的权限不再计入用户有效权限集。
    内置角色不可禁用（``RBAC.BUILTIN_ROLE_PROTECTED``）。
    """

    return await use_case.disable_role(ctx, role_id)


@router.delete(
    "/roles/{roleId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除角色",
    operation_id="delete_role",
)
async def delete_role(
    ctx: RoleWriteCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> Response:
    """删除角色 — SPEC 13.2.

    SPEC 13.2: "系统内置角色具有明确保护规则"。
    内置角色不可删除（``RBAC.BUILTIN_ROLE_PROTECTED``）。
    有用户关联的角色不可删除（``RBAC.ROLE_HAS_USERS``）。
    """

    await use_case.delete_role(ctx, role_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/roles/{roleId}/permissions",
    response_model=RoleDetailResponse,
    summary="为角色分配权限点",
    operation_id="assign_role_permissions",
)
async def assign_role_permissions(
    request_body: AssignPermissionsRequest,
    ctx: AssignmentWriteCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> RoleDetailResponse:
    """为角色分配权限点（全量替换）— SPEC 13.2.

    分配不存在的权限编码返回 400（``RBAC.PERMISSION_NOT_FOUND``）。
    """

    return await use_case.assign_permissions(ctx, role_id, request_body)


@router.get(
    "/roles/{roleId}/members",
    response_model=PageResponse[RoleMemberResponse],
    summary="查询角色成员",
    operation_id="get_role_members",
)
async def get_role_members(
    ctx: RoleReadCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
    params: Annotated[PageParams, Depends()],
) -> dict[str, object]:
    """分页查询角色成员 — SPEC 13.2."""

    return await use_case.get_role_members(
        ctx,
        role_id,
        page=params.page,
        page_size=params.page_size,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 用户角色 — /users/{userId}/roles
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/users/{userId}/roles",
    summary="查询用户角色",
    operation_id="get_user_roles",
)
async def get_user_roles(
    ctx: AssignmentWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> dict[str, object]:
    """查询用户的角色列表 — SPEC 13.2.

    通过 identity 模块 Port 校验用户存在性。
    不存在返回 404（``USER.NOT_FOUND``）。
    """

    return await use_case.get_user_roles(ctx, user_id)


@router.put(
    "/users/{userId}/roles",
    summary="为用户分配角色",
    operation_id="assign_user_roles",
)
async def assign_user_roles(
    request_body: AssignUserRolesRequest,
    ctx: AssignmentWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> dict[str, object]:
    """为用户分配角色（全量替换）— SPEC 13.2.

    通过 identity 模块 Port 校验用户存在性。
    """

    return await use_case.assign_user_roles(ctx, user_id, request_body)


@router.delete(
    "/users/{userId}/roles/{roleId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移除用户角色",
    operation_id="remove_user_role",
)
async def remove_user_role(
    ctx: AssignmentWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> Response:
    """移除用户角色 — SPEC 13.2.

    用户角色未分配返回 409（``RBAC.USER_ROLE_NOT_ASSIGNED``）。
    """

    await use_case.remove_user_role(ctx, user_id, role_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
