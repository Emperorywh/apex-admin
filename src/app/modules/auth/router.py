"""认证模块 Router — API 层（SPEC 5.2 / 9.1 / 12.1 / 12.2 / 12.3 / 12.4）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository"。

路由组织（SPEC 9.1）:
  公开端点 — ``/auth`` 前缀:
    POST   /auth/login               登录（返回 Access Token + Set-Cookie）
    POST   /auth/refresh             刷新 Access Token（Set-Cookie 新 Refresh Token）

  受保护端点 — 需要认证依赖:
    POST   /auth/logout               退出当前会话（删除 Cookie）
    POST   /auth/logout-others        退出其他会话
    GET    /auth/sessions             查看活动会话列表

  管理端点 — 需要认证 + 权限校验:
    POST   /auth/users/{user_id}/force-offline  管理员强制用户下线

SPEC 12.4: 登录和刷新响应必须设置 ``Cache-Control: no-store``。
SPEC 12.1: Access Token 仅在登录/刷新响应体中返回一次。
SPEC 12.2: Refresh Token 仅经 Set-Cookie 下发，不进入 JSON 响应。
SPEC 12.4: Refresh/Logout 校验 Origin 精确匹配白名单。
"""

from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Path, Request, Response, status

from app.application.context import (
    UseCaseContext,  # noqa: TC001 — FastAPI 运行时需要解析
)
from app.core.api.pagination import PageResponse
from app.core.errors.exceptions import AuthorizationError
from app.modules.auth.constants import (
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    REFRESH_COOKIE_SAMESITE,
    SESSION_ABSOLUTE_TIMEOUT,
)
from app.modules.auth.dependencies import (
    AuthenticatedContext,  # noqa: TC001 — FastAPI 运行时需要解析
)
from app.modules.auth.permission import require_permission
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RefreshResponse,
    SessionResponse,
)
from app.modules.auth.use_case import AuthUseCase

router = APIRouter(prefix="/auth", tags=["auth"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_auth_use_case(request: Request) -> AuthUseCase:
    """构造或获取 ``AuthUseCase`` — 组合根装配（SPEC 5.2）.

    SPEC 5.2: "Composition Root 是唯一允许同时引用接口与具体实现
    并执行装配的位置"。此函数从 ``app.state`` 获取预构造的 AuthUseCase，
    或在首次调用时构造。

    Router 通过 ``Depends(get_auth_use_case)`` 获得 Use Case 实例，
    不直接接触 UoW、Repository 或 AsyncSession（SPEC 5.6）。
    """

    use_case: AuthUseCase | None = getattr(request.app.state, "auth_use_case", None)
    if use_case is not None:
        return use_case

    # 首次调用时构造 — 组合根装配
    from app.application.ports import SystemClock, UuidGenerator
    from app.core.security.digest import TokenDigestService
    from app.core.security.password import Argon2Hasher
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter
    from app.modules.audit.security_log import StructlogSecurityLogger
    from app.modules.auth.use_case import AuthUseCase as _AuthUseCase
    from app.modules.identity import adapter as _identity_adapter

    settings = request.app.state.settings
    engine = request.app.state.db_engine

    # 构造 Token 摘要服务 — SPEC 12.2
    access_key = settings.ACCESS_TOKEN_HMAC_KEY
    assert access_key is not None
    refresh_key = settings.REFRESH_TOKEN_HMAC_KEY
    assert refresh_key is not None
    digest_service = TokenDigestService(
        access_key=access_key.get_secret_value().encode("utf-8"),
        refresh_key=refresh_key.get_secret_value().encode("utf-8"),
    )

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造用户认证 Port — SPEC 5.2 跨模块."""

        return _identity_adapter.SqlAlchemyUserAuthAdapter(session)

    def login_log_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造登录日志 Port — SPEC 18.1."""

        return _audit_adapter.SqlAlchemyLoginLogRepository(session)

    def security_log_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造安全日志 Port — SPEC 5.7."""

        return StructlogSecurityLogger()

    use_case = _AuthUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        hasher=Argon2Hasher(),
        digest_service=digest_service,
        user_auth_port_factory=user_auth_port_factory,
        login_log_factory=login_log_factory,
        security_log_factory=security_log_factory,
    )

    # 缓存到 app.state
    request.app.state.auth_use_case = use_case
    return use_case


UseCaseDep = Annotated[AuthUseCase, Depends(get_auth_use_case)]


# ── Origin 校验 — SPEC 12.4 ──────────────────────────────────────────────


def _validate_origin(request: Request) -> None:
    """校验请求 Origin 是否精确匹配部署配置白名单 — SPEC 12.4.

    SPEC 12.4: "Refresh、Logout 等读取 Cookie 的状态变更接口必须校验
    Origin 是否精确匹配部署配置白名单"。

    缺少 Origin 头或不在白名单中时返回 403 Forbidden。
    """

    origin = request.headers.get("origin")
    if origin is None:
        raise AuthorizationError("缺少 Origin 头")

    settings = request.app.state.settings
    if origin not in settings.allowed_origin_set:
        raise AuthorizationError("非法 Origin")


def _set_refresh_cookie(response: Response, raw_refresh_token: str) -> None:
    """设置 ``__Host-apex_refresh`` Cookie — SPEC 12.4.

    SPEC 12.4: Cookie 固定命名 ``__Host-apex_refresh``，设置 ``Secure``、
    ``HttpOnly``、``SameSite=Strict``、``Path=/``，不得设置 ``Domain``。
    ``__Host-`` 前缀要求浏览器强制这些属性。
    本地开发经 ``localhost`` 可信来源规则在 HTTP 下使用 Secure。
    """

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_refresh_token,
        max_age=int(SESSION_ABSOLUTE_TIMEOUT.total_seconds()),
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite=cast("Literal['strict']", REFRESH_COOKIE_SAMESITE),
    )


def _delete_refresh_cookie(response: Response) -> None:
    """删除 ``__Host-apex_refresh`` Cookie — SPEC 12.4.

    SPEC 12.4: "Logout 必须吊销服务端会话并使用相同 Cookie 属性
    删除客户端 Cookie"。
    """

    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite=cast("Literal['strict']", REFRESH_COOKIE_SAMESITE),
    )


# ── 公开端点 ────────────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="账号密码登录",
    operation_id="auth_login",
)
async def login(
    request: Request,
    request_body: LoginRequest,
    use_case: UseCaseDep,
) -> Response:
    """账号密码登录 — SPEC 12.1 / 12.2 / 12.4.

    SPEC 12.1: 登录成功创建服务端会话，返回不透明 Access Token。
    SPEC 12.2: 登录成功创建 Refresh Token Family，经 Set-Cookie 下发。
    SPEC 12.4: 登录响应必须设置 ``Cache-Control: no-store``。
    SPEC 12.1: Access Token 仅在响应体中返回一次。
    SPEC 12.4: Refresh Token 仅经 Set-Cookie 下发，不进入 JSON 响应。

    登录失败返回 401（``AUTH.INVALID_CREDENTIALS``），所有失败路径
    返回完全一致的响应，防止账号枚举（SPEC 12.4）。
    """

    # 提取客户端 IP 和 User-Agent
    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    request_id = str(request.scope.get("request_id", ""))

    result = await use_case.login(
        request_body,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
    )

    # SPEC 12.4: Cache-Control: no-store
    # SPEC 12.2: Refresh Token 仅经 Set-Cookie，不进入 JSON 响应
    login_response = LoginResponse(
        access_token=result.access_token,
        token_type="Bearer",
        expires_in=result.expires_in,
    )
    response = Response(
        content=login_response.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
        headers={"Cache-Control": "no-store"},
    )
    _set_refresh_cookie(response, result.refresh_token)
    return response


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="刷新 Access Token",
    operation_id="auth_refresh",
)
async def refresh(
    request: Request,
    use_case: UseCaseDep,
    refresh_token: str | None = Cookie(
        default=None,
        alias=REFRESH_COOKIE_NAME,
    ),
) -> Response:
    """Refresh Token 轮换 — SPEC 12.2 / 12.4.

    SPEC 12.2: 使用 Refresh Token 获取新的 Access Token。
    旧 Refresh Token 立即失效，新 Refresh Token 仅经 Set-Cookie 下发。
    SPEC 12.4: 校验 Origin 精确匹配白名单（防止 CSRF）。
    SPEC 12.4: 刷新响应必须设置 ``Cache-Control: no-store``。
    SPEC 12.2: Refresh Token 不进入 JSON 响应。

    刷新失败返回 401（``AUTH.REFRESH_FAILED``），不泄露失败原因。
    """

    # SPEC 12.4: Origin 校验
    _validate_origin(request)

    # SPEC 12.2: 必须携带 Refresh Token Cookie
    if refresh_token is None:
        from app.modules.auth.errors import RefreshFailedError

        raise RefreshFailedError("刷新令牌无效")

    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    request_id = str(request.scope.get("request_id", ""))

    result = await use_case.refresh(
        refresh_token,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
    )

    # SPEC 12.4: Cache-Control: no-store
    # SPEC 12.2: 新 Refresh Token 仅经 Set-Cookie，不进入 JSON 响应
    refresh_response = RefreshResponse(
        access_token=result.access_token,
        token_type="Bearer",
        expires_in=result.expires_in,
    )
    response = Response(
        content=refresh_response.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
        headers={"Cache-Control": "no-store"},
    )
    _set_refresh_cookie(response, result.refresh_token)
    return response


# ── 受保护端点 ──────────────────────────────────────────────────────────────


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="退出当前会话",
    operation_id="auth_logout",
)
async def logout(
    request: Request,
    ctx: AuthenticatedContext,
    use_case: UseCaseDep,
) -> Response:
    """退出当前会话 — SPEC 12.3 / 12.4.

    SPEC 12.3: "用户可以退出当前会话"。
    仅吊销当前会话，不影响其他会话。
    SPEC 12.4: 校验 Origin 并删除客户端 Cookie。
    """

    # SPEC 12.4: Origin 校验
    _validate_origin(request)

    assert ctx.actor_id is not None
    assert ctx.session_id is not None

    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    result = await use_case.logout_current(
        session_id=UUID(ctx.session_id),
        user_id=UUID(ctx.actor_id),
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=ctx.request_id,
    )

    response = Response(
        content=result.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
    # SPEC 12.4: 使用相同 Cookie 属性删除客户端 Cookie
    _delete_refresh_cookie(response)
    return response


@router.post(
    "/logout-others",
    response_model=LogoutResponse,
    summary="退出其他会话",
    operation_id="auth_logout_others",
)
async def logout_others(
    request: Request,
    ctx: AuthenticatedContext,
    use_case: UseCaseDep,
) -> LogoutResponse:
    """退出其他会话 — SPEC 12.3.

    SPEC 12.3: "用户可以退出其他会话"。
    吊销除当前会话外的所有活动会话。
    """

    assert ctx.actor_id is not None
    assert ctx.session_id is not None

    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    return await use_case.logout_other(
        current_session_id=UUID(ctx.session_id),
        user_id=UUID(ctx.actor_id),
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=ctx.request_id,
    )


@router.get(
    "/sessions",
    response_model=PageResponse[SessionResponse],
    summary="查看活动会话列表",
    operation_id="auth_list_sessions",
)
async def list_sessions(
    ctx: AuthenticatedContext,
    use_case: UseCaseDep,
) -> dict[str, object]:
    """查看当前用户的活动会话列表 — SPEC 12.3.

    SPEC 12.3: "用户可以查看自己的活动会话"。
    仅返回当前用户的活动（未吊销）会话。
    """

    assert ctx.actor_id is not None

    return await use_case.list_sessions(
        user_id=UUID(ctx.actor_id),
    )


# ── 管理端点 — 管理员强制下线（SPEC 12.3 / 18.1）────────────────────────────


@router.post(
    "/users/{user_id}/force-offline",
    summary="管理员强制用户下线",
    operation_id="auth_force_offline",
)
async def force_offline(
    request: Request,
    ctx: Annotated[
        UseCaseContext,
        Depends(require_permission("system:user:write")),
    ],
    use_case: UseCaseDep,
    user_id: Annotated[UUID, Path(description="目标用户 ID")],
) -> dict[str, object]:
    """管理员强制用户下线 — SPEC 12.3 / 18.1.

    SPEC 12.3: "管理员可以强制用户下线"。
    吊销目标用户的全部活动会话，使目标用户下一个请求立即收到 401。
    记录登录日志（SPEC 18.1）。

    此端点属于管理接口，通过权限依赖保护（SPEC 23.5）。
    """

    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    count = await use_case.force_offline(
        ctx,
        user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return {"user_id": str(user_id), "revoked_sessions": count}


# ── 辅助函数 ────────────────────────────────────────────────────────────────


def _get_client_ip(request: Request) -> str:
    """提取客户端 IP 地址 — SPEC 12.4.

    生产环境通过 Nginx 代理时，可信 IP 从 ``X-Forwarded-For`` 提取
    （G4 实现）。当前 G2 直接使用 ``request.client.host``。
    """

    client = request.client
    if client is not None:
        return client.host
    return "unknown"
