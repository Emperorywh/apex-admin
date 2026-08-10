"""认证模块错误码与异常 — SPEC 10.1 / 10.2 / 12.1 / 12.4.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间。

SPEC 12.4: "防止通过错误响应枚举有效用户"。
``AUTH.INVALID_CREDENTIALS`` 用于所有登录失败场景（密码错误、用户不存在、
已锁定），确保响应完全一致，降低基于响应差异的账号枚举风险。

SPEC 12.3: "用户禁用、角色禁用、权限移除或会话吊销提交后，
后续请求立即按新状态拒绝"。认证依赖对无/错 Token 返回稳定 401 错误码。
``AUTH.UNAUTHENTICATED`` 用于认证依赖的所有拒绝场景。
"""

from __future__ import annotations

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import AuthenticationError

# ── 错误码常量 ──────────────────────────────────────────────────────────────

#: 登录凭据无效 — SPEC 12.4: 所有登录失败返回相同错误码，防止枚举。
AUTH_INVALID_CREDENTIALS = "AUTH.INVALID_CREDENTIALS"

#: 未认证 — SPEC 12.3: 认证依赖对无/错 Token 返回的稳定 401 错误码。
AUTH_UNAUTHENTICATED = "AUTH.UNAUTHENTICATED"

#: 会话不存在 — 退出或查看不存在会话时使用。
AUTH_SESSION_NOT_FOUND = "AUTH.SESSION_NOT_FOUND"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class InvalidCredentialsError(AuthenticationError):
    """登录凭据无效 — HTTP 401.

    SPEC 12.4: "防止通过错误响应枚举有效用户"。
    所有登录失败场景（密码错误、用户不存在、账号或 IP 被锁定）均抛出此异常，
    返回完全一致的错误码和 HTTP 状态码，降低账号枚举风险。

    调用方无法从此异常判断是用户名不存在、密码错误还是被锁定。
    """

    code = AUTH_INVALID_CREDENTIALS


class SessionNotFoundError(AuthenticationError):
    """会话不存在 — HTTP 401.

    退出或查看会话时，指定会话不存在或不属于当前用户时使用。
    使用 HTTP 401 而非 404，避免泄露会话存在性信息。
    """

    code = AUTH_SESSION_NOT_FOUND


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    AUTH_INVALID_CREDENTIALS,
    401,
    meaning="登录凭据无效",
    scenario="登录失败（密码错误、用户不存在或被锁定），SPEC 12.4 防枚举",
)
default_registry.register(
    AUTH_SESSION_NOT_FOUND,
    401,
    meaning="会话不存在",
    scenario="退出或查看不存在的会话",
)
