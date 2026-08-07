"""认证模块路由（SPEC §5.2、§9.1、§12.1、§12.3、§12.4）。

Router 挂载在 ``/api/v1/auth`` 前缀下，提供登录和登出端点。

登录端点（``POST /api/v1/auth/login``）：
- 接受用户名/密码请求体
- 验证 Argon2id 密码、创建会话、生成 Access Token 和 Refresh Token
- Access Token 在响应体中返回一次
- Refresh Token 通过 ``__Host-apex_refresh`` HttpOnly Cookie 设置
- 响应设置 ``Cache-Control: no-store``（SPEC §12.1、§12.2）

登出端点（``POST /api/v1/auth/logout``）：
- 从 Cookie 读取 Refresh Token
- 吊销会话并删除 Cookie（SPEC §12.4）

Cookie 属性（SPEC §12.4）：
- 名称 ``__Host-apex_refresh``
- ``Secure``、``HttpOnly``、``SameSite=Strict``、``Path=/``
- 不设置 ``Domain``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config.settings import Settings
from app.modules.auth.application.schemas import LoginRequest, LoginResponse
from app.modules.auth.application.service import AuthService
from app.modules.auth.domain.model import ABSOLUTE_TIMEOUT_HOURS
from app.modules.auth.infrastructure.wiring import create_auth_service

# Refresh Token Cookie 名称（SPEC §12.4）
REFRESH_TOKEN_COOKIE_NAME = "__Host-apex_refresh"

auth_router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------


def _get_engine(request: Request) -> AsyncEngine:
    """从应用状态获取数据库引擎。"""
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


def _get_settings(request: Request) -> Settings:
    """从应用状态获取部署配置。"""
    return cast("Settings", request.app.state.settings)


def get_auth_service(request: Request) -> AuthService:
    """FastAPI 依赖：装配并返回认证服务实例。"""
    engine = _get_engine(request)
    settings = _get_settings(request)
    return create_auth_service(engine, settings)


def _get_client_ip(request: Request) -> str:
    """从请求中提取客户端 IP。

    使用 ``request.client.host`` 作为可信客户端 IP。G4 反向代理场景
    下的可信代理头处理由 SPEC §23.1 单独定义。
    """
    if request.client is not None:
        return request.client.host
    return "unknown"


def _get_user_agent(request: Request) -> str:
    """从请求中提取 User-Agent。"""
    return request.headers.get("user-agent", "")


def _set_refresh_token_cookie(response: Response, refresh_token: str) -> None:
    """设置 Refresh Token Cookie（SPEC §12.4）。

    Cookie 属性：``__Host-`` 前缀要求 ``Secure``、``Path=/``、无 ``Domain``。
    ``Max-Age`` 不超过会话绝对超时时间（12 小时）（SPEC §12.3）。
    """
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        max_age=ABSOLUTE_TIMEOUT_HOURS * 3600,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def _delete_refresh_token_cookie(response: Response) -> None:
    """删除 Refresh Token Cookie（SPEC §12.4）。

    使用与设置时相同的 Cookie 属性删除客户端 Cookie。
    """
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@auth_router.post(
    "/login",
    summary="账号密码登录",
    description=(
        "使用用户名和密码登录。验证 Argon2id 密码后创建服务端会话，"
        "生成 Access Token（响应体返回一次）和 Refresh Token（HttpOnly Cookie）。"
        "用户不存在时执行固定 Argon2id 虚拟哈希校验以降低响应时间差。"
        "响应设置 Cache-Control: no-store。"
    ),
)
async def login(
    request_body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),  # noqa: B008
) -> LoginResponse:
    """账号密码登录（SPEC §12.1、§12.3）。"""
    ip = _get_client_ip(request)
    user_agent = _get_user_agent(request)

    result = await service.login(
        username=request_body.username,
        password=request_body.password,
        ip=ip,
        user_agent=user_agent,
        device=None,
        current_time=datetime.now(UTC),
    )

    # 设置 Refresh Token Cookie（SPEC §12.4）
    _set_refresh_token_cookie(response, result.refresh_token)

    # Cache-Control: no-store（SPEC §12.1、§12.2）
    response.headers["Cache-Control"] = "no-store"

    return LoginResponse(
        access_token=result.access_token,
        token_type="Bearer",
        expires_in=result.access_token_expires_in,
        session_id=result.session_id,
    )


@auth_router.post(
    "/logout",
    summary="退出登录",
    description=(
        "从 Cookie 读取 Refresh Token，吊销服务端会话并删除 Cookie。Token 无效或已吊销时幂等成功。"
    ),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(  # noqa: B008
        default=None,
        alias=REFRESH_TOKEN_COOKIE_NAME,
    ),
    service: AuthService = Depends(get_auth_service),  # noqa: B008
) -> None:
    """退出登录（SPEC §12.3、§12.4）。"""
    # 无 Cookie → 幂等成功，仍删除 Cookie
    if refresh_token is None:
        _delete_refresh_token_cookie(response)
        return

    await service.logout(
        refresh_token=refresh_token,
        current_time=datetime.now(UTC),
    )
    _delete_refresh_token_cookie(response)
