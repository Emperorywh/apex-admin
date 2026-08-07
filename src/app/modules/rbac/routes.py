"""RBAC 模块路由（SPEC §5.2、§9.1、§13.2、§13.3、§23.5）。

Router 挂载在 ``/api/v1/roles`` 和 ``/api/v1/users`` 前缀下，
提供角色管理和用户-角色分配 API。

所有管理端点通过 :func:`~app.modules.rbac.dependencies.require_permission`
声明所需权限点（SPEC §23.5：所有管理接口具有权限点）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.pagination import Page, PaginationParams, get_pagination_params, paginate
from app.modules.rbac.application.port import AuthenticatedUser
from app.modules.rbac.application.schemas import (
    AssignPermissionsRequest,
    AssignRolesRequest,
    CreateRoleRequest,
    PermissionListResponse,
    RemoveRolesRequest,
    RoleMemberListResponse,
    RoleResponse,
    UpdateRoleRequest,
)
from app.modules.rbac.application.service import RbacService
from app.modules.rbac.dependencies import require_permission
from app.modules.rbac.infrastructure.wiring import create_rbac_service

# ---------------------------------------------------------------------------
# 角色管理路由——/roles
# ---------------------------------------------------------------------------

roles_router = APIRouter(prefix="/roles", tags=["rbac"])

# ---------------------------------------------------------------------------
# 用户-角色分配路由——/users（追加到用户模块的 /users 路由）
# ---------------------------------------------------------------------------

user_role_router = APIRouter(prefix="/users", tags=["rbac"])


# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------


def _get_engine(request: Request) -> AsyncEngine:
    """从应用状态获取数据库引擎。"""
    from app.modules.rbac.dependencies import _get_engine as _engine

    return _engine(request)


def get_rbac_service(request: Request) -> RbacService:
    """FastAPI 依赖：装配并返回 RBAC 服务实例。"""
    engine = _get_engine(request)
    return create_rbac_service(engine)


# ---------------------------------------------------------------------------
# 角色管理端点（SPEC §13.2）
# ---------------------------------------------------------------------------


@roles_router.post(
    "",
    summary="创建角色",
    description="创建新角色。编码全局唯一。成功返回 HTTP 201。",
    status_code=status.HTTP_201_CREATED,
)
async def create_role(
    request_body: CreateRoleRequest,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:create")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> RoleResponse:
    """创建角色（SPEC §13.2）。"""
    role = await service.create_role(
        code=request_body.code,
        name=request_body.name,
        description=request_body.description,
        is_super_admin=request_body.is_super_admin,
        current_time=datetime.now(UTC),
        actor_id=current_user.user_id,
    )
    return _to_role_response(role)


@roles_router.get(
    "",
    summary="分页查询角色列表",
    description="分页查询全部角色，按创建时间降序排列。",
)
async def list_roles(
    pagination: PaginationParams = Depends(get_pagination_params),  # noqa: B008
    current_user: AuthenticatedUser = Depends(require_permission("system:role:read")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> Page[RoleResponse]:
    """分页查询角色列表（SPEC §13.2）。"""
    roles, total = await service.list_roles(
        page=pagination.page,
        page_size=pagination.page_size,
    )
    responses = [_to_role_response(r) for r in roles]
    return paginate(responses, total, pagination)


@roles_router.get(
    "/{role_id}",
    summary="查询角色详情",
)
async def get_role(
    role_id: UUID,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:read")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> RoleResponse:
    """查询角色详情（SPEC §13.2）。"""
    role = await service.get_role(role_id)
    return _to_role_response(role)


@roles_router.patch(
    "/{role_id}",
    summary="更新角色",
    description="部分更新角色名称和描述。不允许修改编码、状态和标志。",
)
async def update_role(
    role_id: UUID,
    request_body: UpdateRoleRequest,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:update")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> RoleResponse:
    """更新角色（SPEC §13.2）。"""
    field_updates = request_body.model_dump(exclude_unset=True)
    role = await service.update_role(
        role_id=role_id,
        field_updates=field_updates,
        current_time=datetime.now(UTC),
        actor_id=current_user.user_id,
    )
    return _to_role_response(role)


@roles_router.post(
    "/{role_id}/enable",
    summary="启用角色",
)
async def enable_role(
    role_id: UUID,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:enable")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> RoleResponse:
    """启用角色（SPEC §13.2）。"""
    role = await service.enable_role(
        role_id=role_id,
        current_time=datetime.now(UTC),
        actor_id=current_user.user_id,
    )
    return _to_role_response(role)


@roles_router.post(
    "/{role_id}/disable",
    summary="禁用角色",
    description="禁用角色。内置超级管理员角色不可禁用。",
)
async def disable_role(
    role_id: UUID,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:disable")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> RoleResponse:
    """禁用角色（SPEC §13.2、§13.4）。"""
    role = await service.disable_role(
        role_id=role_id,
        current_time=datetime.now(UTC),
        actor_id=current_user.user_id,
    )
    return _to_role_response(role)


# ---------------------------------------------------------------------------
# 角色-权限分配端点（SPEC §13.2）
# ---------------------------------------------------------------------------


@roles_router.put(
    "/{role_id}/permissions",
    summary="为角色分配权限",
    description=(
        "全量替换角色的权限点集合。普通管理员只能授予自身范围内的权限。超级管理员不受限。"
    ),
)
async def assign_permissions(
    role_id: UUID,
    request_body: AssignPermissionsRequest,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:assign_permissions")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> PermissionListResponse:
    """为角色分配权限点（SPEC §13.2、§13.3）。"""
    codes = await service.assign_permissions_to_role(
        role_id=role_id,
        permission_codes=frozenset(request_body.permission_codes),
        current_time=datetime.now(UTC),
        actor_id=current_user.user_id,
    )
    return PermissionListResponse(permission_codes=sorted(codes))


@roles_router.get(
    "/{role_id}/permissions",
    summary="查询角色权限",
)
async def get_role_permissions(
    role_id: UUID,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:read")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> PermissionListResponse:
    """查询角色的权限点编码集合（SPEC §13.2）。"""
    codes = await service.get_role_permissions(role_id)
    return PermissionListResponse(permission_codes=sorted(codes))


# ---------------------------------------------------------------------------
# 角色成员端点（SPEC §13.2）
# ---------------------------------------------------------------------------


@roles_router.get(
    "/{role_id}/members",
    summary="查询角色成员",
    description="查询指定角色的全部成员用户 ID。",
)
async def get_role_members(
    role_id: UUID,
    current_user: AuthenticatedUser = Depends(require_permission("system:role:read_members")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> RoleMemberListResponse:
    """查询角色成员（SPEC §13.2）。"""
    user_ids = await service.get_role_members(role_id)
    return RoleMemberListResponse(user_ids=user_ids)


# ---------------------------------------------------------------------------
# 用户-角色分配端点（SPEC §13.2）
# ---------------------------------------------------------------------------


@user_role_router.put(
    "/{user_id}/roles",
    summary="为用户分配角色",
    description=(
        "增量分配：在用户现有角色基础上追加指定角色。"
        "普通管理员只能授予自身范围内的角色，只能管理范围是自身子集的用户。"
        "超级管理员不受限。"
    ),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def assign_roles_to_user(
    user_id: UUID,
    request_body: AssignRolesRequest,
    current_user: AuthenticatedUser = Depends(require_permission("system:user:assign_roles")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> None:
    """为用户分配角色（SPEC §13.2、§13.3）。"""
    await service.assign_roles_to_user(
        user_id=user_id,
        role_codes=frozenset(request_body.role_codes),
        current_time=datetime.now(UTC),
        actor_id=current_user.user_id,
    )


@user_role_router.delete(
    "/{user_id}/roles",
    summary="移除用户角色",
    description=(
        "移除用户的指定角色。禁止移除导致系统失去最后一个可用超级管理员的操作。"
        "普通管理员只能管理范围是自身子集的用户。"
    ),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_roles_from_user(
    user_id: UUID,
    request_body: RemoveRolesRequest,
    current_user: AuthenticatedUser = Depends(require_permission("system:user:assign_roles")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> None:
    """移除用户角色（SPEC §13.2、§13.3、§13.4）。"""
    await service.remove_roles_from_user(
        user_id=user_id,
        role_codes=frozenset(request_body.role_codes),
        current_time=datetime.now(UTC),
        actor_id=current_user.user_id,
    )


@user_role_router.get(
    "/{user_id}/roles",
    summary="查询用户角色",
)
async def get_user_roles(
    user_id: UUID,
    current_user: AuthenticatedUser = Depends(require_permission("system:user:assign_roles")),  # noqa: B008
    service: RbacService = Depends(get_rbac_service),  # noqa: B008
) -> list[RoleResponse]:
    """查询用户的角色列表（SPEC §13.2）。"""
    roles = await service.get_user_roles(user_id)
    return [_to_role_response(r) for r in roles]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_role_response(role: object) -> RoleResponse:
    """将领域实体转换为响应 Schema。"""
    return RoleResponse(
        id=role.id,  # type: ignore[attr-defined]
        code=role.code,  # type: ignore[attr-defined]
        name=role.name,  # type: ignore[attr-defined]
        status=str(role.status),  # type: ignore[attr-defined]
        description=role.description,  # type: ignore[attr-defined]
        is_builtin=role.is_builtin,  # type: ignore[attr-defined]
        is_super_admin=role.is_super_admin,  # type: ignore[attr-defined]
        created_at=role.created_at,  # type: ignore[attr-defined]
        created_by=role.created_by,  # type: ignore[attr-defined]
        updated_at=role.updated_at,  # type: ignore[attr-defined]
        updated_by=role.updated_by,  # type: ignore[attr-defined]
    )
