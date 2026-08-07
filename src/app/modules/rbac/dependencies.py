"""统一认证与授权 FastAPI 依赖（SPEC §13.3、§23.5）。

提供两个核心依赖：

1. :func:`get_current_user` — 统一认证依赖：验证 Access Token、加载用户、
   检查会话有效性、加载权限集合（SPEC §13.3）。
2. :func:`require_permission` — 权限检查依赖工厂：路由声明所需权限，
   认证依赖在请求入口检查（SPEC §13.3）。

权限基于 DB 实时加载，不使用 Token 缓存（SPEC §13.3：权限变更在下一请求
立即生效）。

默认拒绝未认证访问（SPEC §23.5）：所有使用 ``Depends(get_current_user)``
或 ``Depends(require_permission(...))`` 的端点在未认证时返回 401。
公共端点（登录、登出、刷新、健康检查）不使用这些依赖，即为显式声明的
公共接口（SPEC §23.5）。

超级管理员绕过权限检查（SPEC §13.4：集中管理），通过角色标志检测，
不使用魔法用户 ID。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config.settings import Settings
from app.modules.auth.application.port import AuthContext
from app.modules.auth.application.service import AuthService
from app.modules.auth.infrastructure.wiring import create_auth_service
from app.modules.rbac.application.port import AuthenticatedUser
from app.modules.rbac.application.service import RbacService
from app.modules.rbac.infrastructure.wiring import create_rbac_service

# ---------------------------------------------------------------------------
# 内部辅助函数
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


def _get_cached_auth_service(request: Request) -> AuthService:
    """获取缓存的 AuthService 实例（避免每请求重新计算虚拟哈希）。

    AuthService 构造时会计算 Argon2id 虚拟哈希（~0.1s），缓存实例
    确保每应用实例只计算一次。
    """
    service = getattr(request.app.state, "shared_auth_service", None)
    if service is not None:
        return cast("AuthService", service)
    engine = _get_engine(request)
    settings = _get_settings(request)
    service = create_auth_service(engine, settings)
    request.app.state.shared_auth_service = service
    return service


def _get_rbac_service(request: Request) -> RbacService:
    """获取 RBAC 服务实例。"""
    engine = _get_engine(request)
    return create_rbac_service(engine)


async def _validate_access_token(request: Request) -> AuthContext:
    """验证 Authorization 头中的 Access Token（SPEC §12.3、§13.3）。

    返回认证上下文（user_id, session_id）。验证失败返回 401。

    默认拒绝未认证访问（SPEC §23.5）。
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少有效的认证凭证",
        )
    access_token = auth_header[7:]

    auth_service = _get_cached_auth_service(request)
    try:
        return await auth_service.validate_access_token(
            access_token=access_token,
            current_time=datetime.now(UTC),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证凭证无效或已过期",
        ) from exc


# ---------------------------------------------------------------------------
# 统一认证依赖（SPEC §13.3）
# ---------------------------------------------------------------------------


async def get_current_user(request: Request) -> AuthenticatedUser:
    """统一认证依赖：验证 Access Token、加载用户、检查会话有效性（SPEC §13.3）。

    流程：
    1. 验证 Access Token 摘要查 DB（SPEC §12.3）
    2. 检查用户启用、会话有效、Token 有效、空闲/绝对过期
    3. 从 DB 加载用户全部启用角色的权限点并集（SPEC §13.2 管理范围）
    4. 判断用户是否拥有超级管理员角色（SPEC §13.4）

    权限基于 DB 实时加载——权限变更事务提交后，后续请求立即读取新权限
    （SPEC §13.3）。

    Returns:
        :class:`AuthenticatedUser`，包含用户 ID、会话 ID、权限集合和超级管理员标志

    Raises:
        HTTPException 401: Token 无效、用户禁用、会话无效或已过期
    """
    auth_ctx = await _validate_access_token(request)
    rbac_service = _get_rbac_service(request)

    permissions = await rbac_service.get_user_permissions(auth_ctx.user_id)
    is_super_admin = await rbac_service.is_user_super_admin(auth_ctx.user_id)

    # 加载角色编码集合
    roles = await rbac_service.get_user_roles(auth_ctx.user_id)
    role_codes = frozenset(r.code for r in roles if r.is_active)

    return AuthenticatedUser(
        user_id=auth_ctx.user_id,
        session_id=auth_ctx.session_id,
        permissions=permissions,
        is_super_admin=is_super_admin,
        role_codes=role_codes,
    )


def require_permission(*permission_codes: str) -> Callable[..., Awaitable[AuthenticatedUser]]:
    """创建权限检查 FastAPI 依赖（SPEC §13.3、§23.5）。

    路由声明所需权限，认证依赖在请求入口检查。

    超级管理员绕过权限检查（SPEC §13.4：集中管理）。
    非超级管理员需要拥有至少一个声明的权限（OR 语义）。

    用法::

        @router.get("/users")
        async def list_users(
            current_user: AuthenticatedUser = Depends(require_permission("system:user:read")),
        ):
            ...

    Args:
        *permission_codes: 所需权限点编码（至少一个）

    Returns:
        FastAPI 依赖函数，返回 :class:`AuthenticatedUser`
    """
    if not permission_codes:
        raise ValueError("require_permission 至少需要一个权限编码")

    required = frozenset(permission_codes)

    async def _check(
        current_user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    ) -> AuthenticatedUser:
        # 超级管理员绕过（SPEC §13.4）
        if current_user.is_super_admin:
            return current_user

        # 检查是否拥有任一所需权限（OR 语义）
        if not (required & current_user.permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权限执行此操作",
            )
        return current_user

    _check.__name__ = f"require_permission_{'_'.join(permission_codes)}"
    return _check
