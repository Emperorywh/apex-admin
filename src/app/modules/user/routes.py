"""用户模块路由（SPEC §5.2、§9.1、§11.1）。

Router 挂载在 ``/api/v1/users`` 和 ``/api/v1/me`` 前缀下，
只获得 Use Case（Application Service），不获得 UoW、AsyncSession
或提交接口（SPEC §5.6）。

管理员路由（``/users``）提供创建、查询、列表、更新、启用、禁用和
重置密码操作。自助路由（``/me``）提供查询资料、更新资料和修改密码操作。

依赖注入通过 ``app.state.db_pool_provider`` 获取数据库引擎，
经 :func:`~app.modules.user.infrastructure.wiring.create_user_service`
装配完整服务链后提供给端点。

认证模块（TASK-015）实现后，``get_current_user_id`` 依赖将替换为
从认证 Token 中提取用户 ID（SPEC §5.8）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.pagination import Page, PaginationParams, get_pagination_params, paginate
from app.modules.user.application.schemas import (
    ChangePasswordRequest,
    CreateUserRequest,
    ResetPasswordRequest,
    UpdateSelfProfileRequest,
    UpdateUserRequest,
    UserResponse,
)
from app.modules.user.application.service import UserService
from app.modules.user.infrastructure.wiring import create_user_service

# ---------------------------------------------------------------------------
# 管理员路由——/users
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/users", tags=["users"])

# ---------------------------------------------------------------------------
# 自助路由——/me
# ---------------------------------------------------------------------------

self_router = APIRouter(prefix="/me", tags=["users"])


# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------


def _get_engine(request: Request) -> AsyncEngine:
    """从应用状态获取数据库引擎。

    Router 端点通过 FastAPI 依赖注入获取引擎，再装配服务。
    数据库未就绪时返回 503。

    Raises:
        HTTPException: 数据库连接池未配置或未初始化时返回 503
    """
    provider = cast(
        "object | None",
        getattr(request.app.state, "db_pool_provider", None),
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库未配置",
        )
    engine = cast("AsyncEngine | None", getattr(provider, "engine", None))
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库连接池未初始化",
        )
    return engine


def get_user_service(request: Request) -> UserService:
    """FastAPI 依赖：装配并返回用户服务实例。

    Router 端点通过 ``Depends(get_user_service)`` 获取服务。
    服务实例轻量（仅持有工厂引用），每次请求构造。
    """
    engine = _get_engine(request)
    return create_user_service(engine)


def get_current_user_id(
    x_user_id: UUID = Header(alias="X-User-Id"),  # noqa: B008
) -> UUID:
    """从请求头获取当前用户 ID。

    TASK-015 实现认证后，此依赖将被替换为从认证 Token 中提取用户 ID
    （SPEC §5.8：Router 将认证结果转换为 UseCaseContext，显式传给 Use Case）。

    Args:
        x_user_id: 请求头 ``X-User-Id`` 中的用户 UUID

    Returns:
        当前用户 UUID
    """
    return x_user_id


# ---------------------------------------------------------------------------
# 管理员端点
# ---------------------------------------------------------------------------


@admin_router.post(
    "",
    summary="创建用户",
    description=(
        "创建新用户。密码使用 Argon2id 哈希存储，"
        "密码最小长度 12 个 Unicode 字符，最大 128 个字符。"
        "成功返回 HTTP 201 和创建的用户资源（不含密码哈希）。"
    ),
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    request_body: CreateUserRequest,
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> UserResponse:
    """创建用户（SPEC §11.1）。"""
    user = await service.create_user(
        username=request_body.username,
        display_name=request_body.display_name,
        password=request_body.password,
        phone=request_body.phone,
        email=request_body.email,
        current_time=datetime.now(UTC),
    )
    return _to_response(user)


@admin_router.get(
    "",
    summary="分页查询用户列表",
    description=(
        "分页查询全部用户，按创建时间降序排列。"
        "响应使用标准分页结构 {items, total, page, page_size, pages}。"
    ),
)
async def list_users(
    pagination: PaginationParams = Depends(get_pagination_params),  # noqa: B008
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> Page[UserResponse]:
    """分页查询用户列表（SPEC §11.1）。"""
    users, total = await service.list_users(
        page=pagination.page,
        page_size=pagination.page_size,
    )
    responses = [_to_response(user) for user in users]
    return paginate(responses, total, pagination)


@admin_router.get(
    "/{user_id}",
    summary="查询用户详情",
    description="按 UUID 查询单个用户详情。响应不含密码哈希。",
)
async def get_user(
    user_id: UUID,
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> UserResponse:
    """查询用户详情（SPEC §11.1）。"""
    user = await service.get_user(user_id)
    return _to_response(user)


@admin_router.patch(
    "/{user_id}",
    summary="更新用户资料",
    description=("部分更新用户资料。仅修改请求中包含的字段。不允许修改用户名、状态和密码。"),
)
async def update_user(
    user_id: UUID,
    request_body: UpdateUserRequest,
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> UserResponse:
    """更新用户基本资料（SPEC §11.1）。"""
    field_updates = request_body.model_dump(exclude_unset=True)
    user = await service.update_user_profile(
        user_id=user_id,
        field_updates=field_updates,
        current_time=datetime.now(UTC),
    )
    return _to_response(user)


@admin_router.post(
    "/{user_id}/enable",
    summary="启用用户",
    description="将用户状态设置为启用。",
)
async def enable_user(
    user_id: UUID,
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> UserResponse:
    """启用用户（SPEC §11.1）。"""
    user = await service.enable_user(
        user_id=user_id,
        current_time=datetime.now(UTC),
    )
    return _to_response(user)


@admin_router.post(
    "/{user_id}/disable",
    summary="禁用用户",
    description=("将用户状态设置为禁用。禁止禁用系统最后一个可用超级管理员。"),
)
async def disable_user(
    user_id: UUID,
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> UserResponse:
    """禁用用户（SPEC §11.1、§13.4）。"""
    user = await service.disable_user(
        user_id=user_id,
        current_time=datetime.now(UTC),
    )
    return _to_response(user)


@admin_router.post(
    "/{user_id}/reset-password",
    summary="管理员重置用户密码",
    description=(
        "管理员重置指定用户的密码。重置后认证模块吊销该用户全部会话。成功返回 HTTP 204，无响应体。"
    ),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reset_password(
    user_id: UUID,
    request_body: ResetPasswordRequest,
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> None:
    """管理员重置用户密码（SPEC §11.1）。"""
    await service.reset_password(
        user_id=user_id,
        new_password=request_body.new_password,
        current_time=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# 自助端点
# ---------------------------------------------------------------------------


@self_router.get(
    "",
    summary="查询当前用户资料",
    description="查询当前登录用户的资料。响应不含密码哈希。",
)
async def get_self_profile(
    current_user_id: UUID = Depends(get_current_user_id),  # noqa: B008
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> UserResponse:
    """用户自助查询资料（SPEC §11.1）。"""
    user = await service.get_self_profile(current_user_id)
    return _to_response(user)


@self_router.patch(
    "",
    summary="更新当前用户资料",
    description=(
        "当前用户更新自身资料。仅允许修改显示名称、手机号和邮箱。不允许修改用户名、状态和密码。"
    ),
)
async def update_self_profile(
    request_body: UpdateSelfProfileRequest,
    current_user_id: UUID = Depends(get_current_user_id),  # noqa: B008
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> UserResponse:
    """用户自助更新资料（SPEC §11.1）。"""
    field_updates = request_body.model_dump(exclude_unset=True)
    user = await service.update_self_profile(
        user_id=current_user_id,
        field_updates=field_updates,
        current_time=datetime.now(UTC),
    )
    return _to_response(user)


@self_router.post(
    "/change-password",
    summary="当前用户修改密码",
    description=("当前用户修改自己的密码。需提供当前密码和新密码。成功返回 HTTP 204，无响应体。"),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def change_password(
    request_body: ChangePasswordRequest,
    current_user_id: UUID = Depends(get_current_user_id),  # noqa: B008
    service: UserService = Depends(get_user_service),  # noqa: B008
) -> None:
    """用户自助修改密码（SPEC §11.1）。"""
    await service.change_password(
        user_id=current_user_id,
        current_password=request_body.current_password,
        new_password=request_body.new_password,
        current_time=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_response(user: object) -> UserResponse:
    """将领域实体转换为响应 Schema。

    显式构建响应，确保密码哈希等敏感字段不进入响应
    （SPEC §9.3、§23.2）。
    """
    return UserResponse(
        id=user.id,  # type: ignore[attr-defined]
        username=user.username,  # type: ignore[attr-defined]
        display_name=user.display_name,  # type: ignore[attr-defined]
        status=str(user.status),  # type: ignore[attr-defined]
        phone=user.phone,  # type: ignore[attr-defined]
        email=user.email,  # type: ignore[attr-defined]
        last_login_at=user.last_login_at,  # type: ignore[attr-defined]
        password_updated_at=user.password_updated_at,  # type: ignore[attr-defined]
        created_at=user.created_at,  # type: ignore[attr-defined]
        created_by=user.created_by,  # type: ignore[attr-defined]
        updated_at=user.updated_at,  # type: ignore[attr-defined]
        updated_by=user.updated_by,  # type: ignore[attr-defined]
    )
