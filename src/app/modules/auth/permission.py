"""权限校验依赖 — SPEC 13.3 / 23.5.

SPEC 13.3: "提供统一的权限校验入口"。
SPEC 13.3: "路由层声明访问所需权限"。
SPEC 13.3: "认证依赖执行请求入口的粗粒度权限校验"。
SPEC 23.5: "默认拒绝未认证访问；公共接口必须显式声明"。

此模块提供 ``require_permission`` 工厂函数。路由层通过::

    @router.get("/users")
    async def list_users(
        ctx: Annotated[UseCaseContext, Depends(require_permission("system:user:read"))],
    ):
        ...

声明访问所需权限点。

依赖执行流程:
  1. 调用认证依赖（``get_authenticated_context_async``）完成身份校验。
  2. ``get_actor_authorization`` 从数据库加载操作者有效权限集和超管标志
     （每请求查库，SPEC 13.3）。
  3. 权限点校验——超管绕过，否则检查权限编码是否在有效集合中。

关键写 Use Case 在自身 UoW 中重新读取授权关系执行二次校验
（SPEC 13.3），此入口级校验为粗粒度。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.application.context import (  # noqa: TC001 — FastAPI 运行时需要解析
    UseCaseContext,
)
from app.core.security.authorization import (
    check_permission,
    is_super_admin,
)
from app.modules.auth.dependencies import get_authenticated_context_async


@dataclass(frozen=True)
class ActorAuthorization:
    """操作者授权信息（入口级）."""

    ctx: UseCaseContext
    permissions: frozenset[str]
    is_super_admin: bool


# ── 可覆盖的 RBAC 数据加载依赖 ──────────────────────────────────────────────


async def get_actor_authorization(
    ctx: Annotated[UseCaseContext, Depends(get_authenticated_context_async)],
    request: Request,
) -> ActorAuthorization:
    """从数据库加载操作者有效权限集和超管标志 — SPEC 13.3.

    每请求查库，不使用 TTL 缓存（SPEC 13.3: "权限变更事务提交后，
    后续受保护请求立即读取并使用新的权限关系"）。

    此函数是 FastAPI 依赖，测试可通过 ``app.dependency_overrides``
    覆盖以模拟不同权限场景。
    """

    from uuid import UUID

    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.rbac.adapter import SqlAlchemyUserRbacAdapter

    assert ctx.actor_id is not None

    engine = request.app.state.db_engine
    actor_uuid: UUID = UUID(ctx.actor_id)

    async with SqlAlchemyUnitOfWork(engine) as uow:
        port = SqlAlchemyUserRbacAdapter(uow.session)
        permissions = await port.get_effective_permission_codes(actor_uuid)
        role_codes = await port.get_role_codes_by_user(actor_uuid)

    return ActorAuthorization(
        ctx=ctx,
        permissions=frozenset(permissions),
        is_super_admin=is_super_admin(role_codes),
    )


# ── 权限校验依赖工厂 ────────────────────────────────────────────────────────


def require_permission(permission_code: str):  # type: ignore[no-untyped-def]
    """权限校验依赖工厂 — SPEC 13.3 / 23.5.

    返回可被 ``Depends`` 包装的异步函数。路由层通过::

        ctx: Annotated[UseCaseContext, Depends(require_permission("code"))]

    声明所需权限点。

    返回的函数上携带 ``__apex_permission__`` 标记，供路由注册测试
    自动扫描验证所有管理接口均声明了权限点（SPEC 23.5 / 34.2）。

    参数:
        permission_code: 路由所需的权限编码。

    返回:
        异步依赖函数，返回已认证的 ``UseCaseContext``。
    """

    async def _check_permission(
        auth: Annotated[ActorAuthorization, Depends(get_actor_authorization)],
    ) -> UseCaseContext:
        """入口级权限校验 — SPEC 13.3."""

        check_permission(
            user_permissions=auth.permissions,
            required_permission=permission_code,
            user_is_super_admin=auth.is_super_admin,
        )
        return auth.ctx

    # 标记权限编码，供路由注册测试扫描（SPEC 23.5 / 34.2）
    _check_permission.__apex_permission__ = permission_code  # type: ignore[attr-defined]
    return _check_permission
