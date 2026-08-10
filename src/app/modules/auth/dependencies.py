"""认证依赖 — FastAPI 依赖函数（SPEC 12.3 / 13.3 / 23.5）.

SPEC 12.3: "每个受保护请求都使用 Access Token 摘要查询 PostgreSQL，
并校验用户启用、会话有效、Token 有效、空闲过期和绝对过期"。

SPEC 13.3: "提供统一的认证依赖"。

SPEC 23.5: "默认拒绝未认证访问"。

认证依赖从 ``Authorization: Bearer <token>`` 头提取不透明 Access Token，
通过 ``AuthUseCase.authenticate`` 查库校验，返回带认证信息的
``UseCaseContext``。

认证失败（无 Token、错 Token、会话吊销、Token 过期、用户禁用等）
统一抛出 ``AuthenticationError``（HTTP 401，稳定错误码 AUTH.UNAUTHENTICATED），
不泄露失败原因差异。
"""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Annotated

from fastapi import Depends, Request

from app.application.context import UseCaseContext
from app.core.errors.exceptions import AuthenticationError

# ── Bearer Token 提取 ────────────────────────────────────────────────────────

_BEARER_PREFIX = "Bearer "


def extract_bearer_token(authorization: str | None) -> str:
    """从 Authorization 头提取 Bearer Token — SPEC 12.3.

    SPEC 12.1: "G2 固定使用不透明随机 Bearer Access Token"。

    参数:
        authorization: Authorization 头值。

    返回:
        不透明 Access Token 字符串。

    抛出:
        AuthenticationError: 头缺失或格式不合法。
    """

    if authorization is None:
        raise AuthenticationError("缺少认证凭证")
    if not authorization.startswith(_BEARER_PREFIX):
        raise AuthenticationError("认证凭证格式不合法")
    token = authorization[len(_BEARER_PREFIX) :].strip()
    if not token:
        raise AuthenticationError("认证凭证为空")
    return token


async def get_authenticated_context_async(
    request: Request,
) -> UseCaseContext:
    """异步认证依赖 — SPEC 12.3 / 13.3.

    从 ``Authorization`` 头提取 Bearer Token，通过 ``AuthUseCase.authenticate``
    查库校验。校验通过时返回带 ``actor_id`` 和 ``session_id`` 的
    ``UseCaseContext``。

    SPEC 12.3: 每请求查库校验用户启用、会话有效、Token 有效、
    空闲过期和绝对过期。

    SPEC 12.3: "用户禁用、角色禁用、权限移除或会话吊销提交后，
    后续请求立即按新状态拒绝"。由于每请求都查库，这些变更提交后
    下一请求立即生效。

    返回:
        带认证信息的 ``UseCaseContext``。

    抛出:
        AuthenticationError: 所有认证失败场景（稳定错误码）。
    """

    # 提取 Authorization 头
    authorization = request.headers.get("Authorization")
    raw_token = extract_bearer_token(authorization)

    # 获取 AuthUseCase
    auth_use_case = _get_auth_use_case(request)

    # 查库认证 — SPEC 12.3
    result = await auth_use_case.authenticate(raw_token)
    if result is None:
        raise AuthenticationError("认证失败")

    user_id, session_id = result

    # 提取请求上下文
    raw_request_id = request.scope.get("request_id", "")
    request_id = str(raw_request_id) if raw_request_id else ""

    return UseCaseContext(
        request_id=request_id,
        actor_id=str(user_id),
        session_id=str(session_id),
        current_time=datetime.now(),  # noqa: DTZ005 — 认证依赖不需要时区精确
        security_metadata=MappingProxyType(
            {"auth_method": "access_token"},
        ),
    )


# ── 组合根装配 ──────────────────────────────────────────────────────────────


def _get_auth_use_case(request: Request):  # type: ignore[no-untyped-def]
    """从 app.state 获取 AuthUseCase — 组合根装配（SPEC 5.2）.

    AuthUseCase 在首次请求时由 Router 的 ``get_auth_use_case`` 构造并
    存入 ``app.state``，此函数从 ``app.state`` 获取实例。

    SPEC 5.2: "Composition Root 是唯一允许同时引用接口与具体实现
    并执行装配的位置"。
    """

    use_case = getattr(request.app.state, "auth_use_case", None)
    if use_case is None:
        raise AuthenticationError("认证服务不可用")
    return use_case


# ── 便捷类型别名 ────────────────────────────────────────────────────────────

#: 已认证上下文依赖 — 在受保护端点通过 ``Depends`` 使用。
AuthenticatedContext = Annotated[
    UseCaseContext,
    Depends(get_authenticated_context_async),
]
