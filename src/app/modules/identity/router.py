"""用户模块 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 11.1）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository 或提交接口"。

Router 通过 FastAPI 依赖注入获得 ``UserUseCase``，
将 HTTP 请求转换为 Use Case 调用，将 Use Case 返回值转换为 HTTP 响应。
Router 不接触 UoW、Repository 或 AsyncSession。

路由命名与 HTTP 方法语义一致（SPEC 9.1）。
创建成功返回 HTTP 201 + Location（SPEC 9.3）。
无响应体的成功返回 HTTP 204（SPEC 9.3）。

路由组织（SPEC 9.1: 按模块分组）:
  管理端 — ``/users`` 前缀:
    POST   /users                    创建用户
    GET    /users                    分页查询
    GET    /users/{userId}          查询详情
    PUT    /users/{userId}          更新资料
    POST   /users/{userId}/enable   启用
    POST   /users/{userId}/disable  禁用
    POST   /users/{userId}/reset-password  重置密码
    DELETE /users/{userId}          物理删除（审计保护）

  自助端点 — ``/users/me`` 路径:
    GET    /users/me                 查询个人资料
    PUT    /users/me                 更新个人资料（白名单字段）
    PUT    /users/me/password        修改密码（校验旧密码）

自助端点路径 ``/users/me`` 必须注册在 ``/users/{userId}`` 之前，
避免 ``me`` 被 UUID 路径参数匹配。
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
from app.core.errors.exceptions import AuthenticationError
from app.modules.auth.dependencies import get_authenticated_context_async
from app.modules.auth.permission import require_permission
from app.modules.identity.models import UserStatus
from app.modules.identity.schemas import (
    SelfChangePasswordRequest,
    SelfProfileUpdateRequest,
    UserCreateRequest,
    UserResetPasswordRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.modules.identity.use_case import UserUseCase

# ── 排序白名单 — SPEC 9.4 ──────────────────────────────────────────────────

_USER_SORT_FIELDS = frozenset({"username", "display_name", "created_at", "updated_at"})

_ROUTER_PREFIX = "/users"

router = APIRouter(prefix=_ROUTER_PREFIX, tags=["identity"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_user_use_case(request: Request) -> UserUseCase:
    """构造 ``UserUseCase`` — 组合根装配（SPEC 5.2）.

    SPEC 5.2: "Composition Root 是唯一允许同时引用接口与具体实现
    并执行装配的位置"。此函数在 API 层执行装配，从 ``app.state``
    获取数据库引擎，构造 UoW 工厂、审计工厂和 Use Case。

    Router 通过 ``Depends(get_user_use_case)`` 获得 Use Case 实例，
    不直接接触 UoW、Repository 或 AsyncSession（SPEC 5.6）。
    """

    from app.application.ports import SystemClock, UuidGenerator
    from app.core.security.password import Argon2Hasher
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter
    from app.modules.auth.handlers import (
        RevokeSessionsOnPasswordReset,
        RevokeSessionsOnUserDisabled,
    )
    from app.modules.org import adapter as _org_adapter
    from app.modules.org.handlers import ClearUserOrgRelationsOnDisabled

    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计 Port — SPEC 5.7 / 5.2.

        SPEC 5.2: 跨模块通过公开接口协作。
        Use Case 依赖 ``AuditPort`` 接口，此处注入审计 Adapter 具体实现，
        避免 Use Case 直接依赖审计模块的 Adapter。
        通过模块导入（而非类名导入）避免 Router 层直接持有 Repository 类型
        （SPEC 5.6: Router 不得获得 Repository）。
        """

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    def user_rbac_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造用户 RBAC Port — SPEC 13.4."""

        from app.modules.rbac.adapter import SqlAlchemyUserRbacAdapter

        return SqlAlchemyUserRbacAdapter(session)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造用户认证信息 Port — SPEC 13.4."""

        from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter

        return SqlAlchemyUserAuthAdapter(session)

    def org_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造组织关系 Port — SPEC 11.1 / 14.3 跨模块聚合."""

        return _org_adapter.SqlAlchemyOrgRepository(session)

    # SPEC 5.7: auth 模块的事务内事件处理器在禁用/重置密码 Use Case 的
    # 事务内同步执行，吊销该用户全部会话（SPEC 12.3）。
    # org 模块的事务内事件处理器在禁用 Use Case 的事务内同步执行，
    # 清除用户全部组织关系（SPEC 14.3）。
    # 处理器在当前 UoW 的 AsyncSession 上执行，与业务数据强一致。
    auth_event_handlers = [
        RevokeSessionsOnUserDisabled(),
        RevokeSessionsOnPasswordReset(),
        ClearUserOrgRelationsOnDisabled(),
    ]

    return UserUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        hasher=Argon2Hasher(),
        event_handlers=auth_event_handlers,
        audit_factory=audit_factory,
        user_rbac_port_factory=user_rbac_port_factory,
        user_auth_port_factory=user_auth_port_factory,
        org_port_factory=org_port_factory,
    )


UseCaseDep = Annotated[UserUseCase, Depends(get_user_use_case)]

# SPEC 13.3 / 23.5: 管理端通过权限依赖保护，自助端点仅认证。
UserReadCtx = Annotated[UseCaseContext, Depends(require_permission("system:user:read"))]
UserWriteCtx = Annotated[
    UseCaseContext, Depends(require_permission("system:user:write"))
]


def require_actor(
    ctx: Annotated[UseCaseContext, Depends(get_authenticated_context_async)],
) -> UseCaseContext:
    """自助端点前置检查——确保已认证（actor_id 非 None）.

    SPEC 23.5: "默认拒绝未认证访问"。
    自助端点通过认证依赖（``get_authenticated_context_async``）保护，
    不需要权限点（SPEC 23.5: 操作自身资源）。
    """

    if ctx.actor_id is None:
        raise AuthenticationError("自助操作需要认证")
    return ctx


AuthenticatedDep = Annotated[UseCaseContext, Depends(require_actor)]


# ═══════════════════════════════════════════════════════════════════════════════
# 自助端点 — 必须注册在 /{userId} 之前
# SPEC 11.1: 用户查询/更新自己的资料、修改自己的密码
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/me",
    response_model=UserResponse,
    summary="查询当前用户资料",
    operation_id="get_self_profile",
)
async def get_self_profile(
    ctx: AuthenticatedDep,
    use_case: UseCaseDep,
) -> UserResponse:
    """查询当前用户自己的资料 — SPEC 11.1: "用户查询自己的资料"."""

    return await use_case.get_self_profile(ctx)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="更新当前用户资料",
    operation_id="update_self_profile",
)
async def update_self_profile(
    request_body: SelfProfileUpdateRequest,
    ctx: AuthenticatedDep,
    use_case: UseCaseDep,
) -> UserResponse:
    """更新当前用户自己的资料 — SPEC 11.1: "用户更新允许自助修改的资料".

    仅允许白名单字段（display_name、phone、email）。
    ``SelfProfileUpdateRequest`` 继承 ``extra="forbid"``，
    携带 username 或 status 等字段的请求返回 422（SPEC 9.2）。
    """

    return await use_case.update_self_profile(ctx, request_body)


@router.put(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="修改当前用户密码",
    operation_id="change_self_password",
)
async def change_self_password(
    request_body: SelfChangePasswordRequest,
    ctx: AuthenticatedDep,
    use_case: UseCaseDep,
) -> Response:
    """修改当前用户自己的密码 — SPEC 11.1: "用户修改自己的密码".

    必须提供旧密码（校验旧密码是否正确）。
    旧密码不正确返回 409（``USER.INVALID_OLD_PASSWORD``）。
    成功返回 204 无响应体（SPEC 9.3）。
    """

    await use_case.change_self_password(ctx, request_body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════════════════════════════════════════════════════
# 管理端 — 用户生命周期
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建用户",
    operation_id="create_user",
)
async def create_user(
    response: Response,
    request_body: UserCreateRequest,
    ctx: UserWriteCtx,
    use_case: UseCaseDep,
) -> UserResponse:
    """创建用户 — HTTP 201 + Location（SPEC 9.3）.

    创建成功返回 201 和资源 Schema，Location 头指向新建用户详情
    （SPEC 9.3: "创建成功返回 HTTP 201，并在适用时返回 Location"）。
    用户名冲突返回 409（``USER.ALREADY_EXISTS``）。
    """

    result = await use_case.create_user(ctx, request_body)
    # Location 头 — SPEC 9.3
    response.headers["Location"] = f"/api/v1/users/{result.id}"
    return result


@router.get(
    "",
    response_model=PageResponse[UserResponse],
    summary="分页查询用户列表",
    operation_id="list_users",
)
async def list_users(
    ctx: UserReadCtx,
    use_case: UseCaseDep,
    params: Annotated[PageParams, Depends()],
    sort: Annotated[
        list[SortField],
        Depends(sort_dependency(_USER_SORT_FIELDS)),
    ],
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="按用户状态筛选（active / disabled）",
        ),
    ] = None,
) -> dict[str, object]:
    """分页查询用户列表 — SPEC 9.4 分页排序.

    分页参数: ``page``（默认 1）、``pageSize``（默认 20）。
    排序参数: ``sort``，逗号分隔，``-`` 前缀降序。
    排序白名单（camelCase）: ``username``、``displayName``、
    ``createdAt``、``updatedAt``。
    筛选参数: ``status``（active / disabled，可选）。
    """

    parsed_status: UserStatus | None = None
    if status_filter is not None:
        parsed_status = UserStatus(status_filter)

    return await use_case.list_users(
        ctx,
        page=params.page,
        page_size=params.page_size,
        sort_fields=sort,
        status_filter=parsed_status,
    )


@router.get(
    "/{userId}",
    response_model=UserResponse,
    summary="查询用户详情",
    operation_id="get_user",
)
async def get_user(
    ctx: UserReadCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> UserResponse:
    """查询用户详情 — 不存在返回 404（``USER.NOT_FOUND``）。"""

    return await use_case.get_user(ctx, user_id)


@router.put(
    "/{userId}",
    response_model=UserResponse,
    summary="更新用户资料",
    operation_id="update_user",
)
async def update_user(
    request_body: UserUpdateRequest,
    ctx: UserWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> UserResponse:
    """更新用户资料（管理端）— SPEC 11.1: "更新用户基本资料"."""

    return await use_case.update_user(ctx, user_id, request_body)


@router.post(
    "/{userId}/enable",
    response_model=UserResponse,
    summary="启用用户",
    operation_id="enable_user",
)
async def enable_user(
    ctx: UserWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> UserResponse:
    """启用用户 — SPEC 11.1: "启用用户".

    已启用用户返回 409（``USER.ALREADY_ACTIVE``）。
    """

    return await use_case.enable_user(ctx, user_id)


@router.post(
    "/{userId}/disable",
    response_model=UserResponse,
    summary="禁用用户",
    operation_id="disable_user",
)
async def disable_user(
    ctx: UserWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> UserResponse:
    """禁用用户 — SPEC 11.1: "禁用用户".

    禁用时发布 ``USER.DISABLED`` 事件，auth 模块（TASK-013）吊销全部会话。
    已禁用用户返回 409（``USER.ALREADY_DISABLED``）。
    """

    return await use_case.disable_user(ctx, user_id)


@router.post(
    "/{userId}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="重置用户密码",
    operation_id="reset_user_password",
)
async def reset_user_password(
    request_body: UserResetPasswordRequest,
    ctx: UserWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> Response:
    """管理员重置用户密码 — SPEC 11.1: "重置用户密码".

    重置后发布 ``USER.PASSWORD_RESET_BY_ADMIN`` 事件，
    auth 模块（TASK-013）吊销该用户全部会话。
    成功返回 204 无响应体（SPEC 9.3）。
    """

    await use_case.reset_password(ctx, user_id, request_body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{userId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="物理删除用户",
    operation_id="delete_user",
)
async def delete_user(
    ctx: UserWriteCtx,
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(alias="userId", description="用户 ID")],
) -> Response:
    """物理删除用户 — SPEC 11.3 删除策略.

    SPEC 11.3:
      - "已产生审计记录的用户不得因物理删除导致审计信息失真"。
      - "默认优先采用禁用或注销，而不是直接删除用户"。

    已产生审计记录的用户物理删除被拒绝（409 ``USER.HAS_AUDIT_RECORDS``）。
    应优先使用禁用（``POST /users/{id}/disable``）替代物理删除。
    无审计记录的用户可物理删除，成功返回 204（SPEC 9.3）。
    """

    await use_case.delete_user(ctx, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
