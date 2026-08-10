"""认证模块 Router — API 层（SPEC 5.2 / 9.1 / 12.1 / 12.3 / 12.4）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository"。

路由组织（SPEC 9.1）:
  公开端点 — ``/auth`` 前缀:
    POST   /auth/login               登录（返回 Access Token，Cache-Control: no-store）

  受保护端点 — 需要认证依赖:
    POST   /auth/logout               退出当前会话
    POST   /auth/logout-others        退出其他会话
    GET    /auth/sessions             查看活动会话列表

SPEC 12.4: 登录响应必须设置 ``Cache-Control: no-store``。
SPEC 12.1: Access Token 仅在登录响应体中返回一次。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.api.pagination import PageResponse
from app.modules.auth.dependencies import (
    AuthenticatedContext,  # noqa: TC001 — FastAPI 运行时需要解析
)
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
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
    from app.modules.audit.adapter import SqlAlchemyLoginLogRepository
    from app.modules.audit.security_log import StructlogSecurityLogger
    from app.modules.auth.use_case import AuthUseCase as _AuthUseCase
    from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter

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

        return SqlAlchemyUserAuthAdapter(session)

    def login_log_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造登录日志 Port — SPEC 18.1."""

        return SqlAlchemyLoginLogRepository(session)

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
    """账号密码登录 — SPEC 12.1 / 12.4.

    SPEC 12.1: 登录成功创建服务端会话，返回不透明 Access Token。
    SPEC 12.4: 登录响应必须设置 ``Cache-Control: no-store``。
    SPEC 12.1: Access Token 仅在响应体中返回一次。

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
    response = Response(
        content=result.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
        headers={"Cache-Control": "no-store"},
    )
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
) -> LogoutResponse:
    """退出当前会话 — SPEC 12.3.

    SPEC 12.3: "用户可以退出当前会话"。
    仅吊销当前会话，不影响其他会话。
    """

    assert ctx.actor_id is not None
    assert ctx.session_id is not None

    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    return await use_case.logout_current(
        session_id=UUID(ctx.session_id),
        user_id=UUID(ctx.actor_id),
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=ctx.request_id,
    )


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
