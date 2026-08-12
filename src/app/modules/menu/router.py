"""菜单模块 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 15.1 / 15.2）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository
或提交接口"。

Router 通过 FastAPI 依赖注入获得 ``MenuUseCase``。

路由组织（SPEC 9.1: 按模块分组）:
  菜单管理 — ``/menus`` 前缀:
    POST   /menus                       创建菜单
    GET    /menus/tree                   查询菜单树
    GET    /menus/{menuId}              查询菜单详情
    PUT    /menus/{menuId}              更新菜单
    POST   /menus/{menuId}/enable       启用菜单
    POST   /menus/{menuId}/disable      禁用菜单
    PUT    /menus/{menuId}/hierarchy    调整层级与排序
    DELETE /menus/{menuId}              删除菜单

  角色菜单分配 — ``/roles`` 前缀:
    PUT    /roles/{roleId}/menus        为角色分配菜单（全量替换）
    DELETE /roles/{roleId}/menus/{menuId} 移除角色单个菜单
    GET    /roles/{roleId}/menus        查询角色已分配菜单

  当前用户菜单与权限 — ``/me`` 前缀:
    GET    /me/menus                     当前用户菜单树
    GET    /me/permissions               当前用户权限编码
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Path, Request, Response, status

from app.application.context import UseCaseContext
from app.modules.auth.dependencies import get_authenticated_context_async
from app.modules.auth.permission import require_permission
from app.modules.menu.schemas import (
    AssignRoleMenusRequest,
    MenuCreateRequest,
    MenuHierarchyRequest,
    MenuResponse,
    MenuTreeResponse,
    MenuUpdateRequest,
    PermissionCodesResponse,
    RoleMenuIdsResponse,
)
from app.modules.menu.use_case import MenuUseCase

router = APIRouter(tags=["menu"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_menu_use_case(request: Request) -> MenuUseCase:
    """构造 ``MenuUseCase`` — 组合根装配（SPEC 5.2）."""

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter
    from app.modules.rbac import adapter as _rbac_adapter

    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    def user_rbac_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造用户 RBAC Port — SPEC 5.2 跨模块."""

        return _rbac_adapter.SqlAlchemyUserRbacAdapter(session)

    return MenuUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=audit_factory,
        user_rbac_port_factory=user_rbac_port_factory,
    )


UseCaseDep = Annotated[MenuUseCase, Depends(get_menu_use_case)]

# SPEC 23.5: 管理端通过权限依赖保护。
MenuReadCtx = Annotated[UseCaseContext, Depends(require_permission("menu:menu:read"))]
MenuWriteCtx = Annotated[UseCaseContext, Depends(require_permission("menu:menu:write"))]
RoleMenuWriteCtx = Annotated[
    UseCaseContext, Depends(require_permission("menu:role_menu:write"))
]

# 当前用户端点仅需认证，不需额外权限点（任何已认证用户均可查询自身菜单与权限）。
AuthCtx = Annotated[UseCaseContext, Depends(get_authenticated_context_async)]


# ═══════════════════════════════════════════════════════════════════════════════
# 菜单管理 — /menus 前缀
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/menus",
    response_model=MenuResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建菜单",
    operation_id="create_menu",
)
async def create_menu(
    response: Response,
    request_body: MenuCreateRequest,
    ctx: MenuWriteCtx,
    use_case: UseCaseDep,
) -> MenuResponse:
    """创建菜单 — HTTP 201 + Location（SPEC 9.3 / 15.1）.

    支持目录/页面/外链类型与路由元数据、可见性配置。
    父菜单不存在或已禁用返回 400（``MENU.INVALID_PARENT``）。
    """

    result = await use_case.create_menu(ctx, request_body)
    response.headers["Location"] = f"/api/v1/menus/{result['id']}"
    return MenuResponse.model_validate(result)


@router.get(
    "/menus/tree",
    response_model=list[MenuTreeResponse],
    summary="查询菜单树",
    operation_id="get_menu_tree",
)
async def get_menu_tree(
    ctx: MenuReadCtx,
    use_case: UseCaseDep,
    include_disabled: Annotated[
        bool,
        "是否包含禁用状态的菜单（默认 true）",
    ] = True,
) -> list[MenuTreeResponse]:
    """查询菜单树 — SPEC 15.1.

    返回完整菜单树（含不可见菜单，管理端需要看到全部）。
    当 ``include_disabled=false`` 时，禁用菜单被排除。
    """

    tree = await use_case.get_menu_tree(ctx, include_disabled=include_disabled)
    return [MenuTreeResponse.model_validate(node) for node in tree]


@router.get(
    "/menus/{menuId}",
    response_model=MenuResponse,
    summary="查询菜单详情",
    operation_id="get_menu_detail",
)
async def get_menu_detail(
    ctx: MenuReadCtx,
    use_case: UseCaseDep,
    menu_id: Annotated[UUID, Path(alias="menuId", description="菜单 ID")],
) -> MenuResponse:
    """查询菜单详情 — SPEC 15.1.

    不存在返回 404（``MENU.NOT_FOUND``）。
    """

    result = await use_case.get_menu_detail(ctx, menu_id)
    return MenuResponse.model_validate(result)


@router.put(
    "/menus/{menuId}",
    response_model=MenuResponse,
    summary="更新菜单",
    operation_id="update_menu",
)
async def update_menu(
    request_body: MenuUpdateRequest,
    ctx: MenuWriteCtx,
    use_case: UseCaseDep,
    menu_id: Annotated[UUID, Path(alias="menuId", description="菜单 ID")],
) -> MenuResponse:
    """更新菜单基本信息 — SPEC 15.1.

    层级调整使用独立端点 ``PUT /menus/{id}/hierarchy``。
    菜单类型不可变更。
    """

    result = await use_case.update_menu(ctx, menu_id, request_body)
    return MenuResponse.model_validate(result)


@router.post(
    "/menus/{menuId}/enable",
    response_model=MenuResponse,
    summary="启用菜单",
    operation_id="enable_menu",
)
async def enable_menu(
    ctx: MenuWriteCtx,
    use_case: UseCaseDep,
    menu_id: Annotated[UUID, Path(alias="menuId", description="菜单 ID")],
) -> MenuResponse:
    """启用菜单 — SPEC 15.1."""

    result = await use_case.enable_menu(ctx, menu_id)
    return MenuResponse.model_validate(result)


@router.post(
    "/menus/{menuId}/disable",
    response_model=MenuResponse,
    summary="禁用菜单",
    operation_id="disable_menu",
)
async def disable_menu(
    ctx: MenuWriteCtx,
    use_case: UseCaseDep,
    menu_id: Annotated[UUID, Path(alias="menuId", description="菜单 ID")],
) -> MenuResponse:
    """禁用菜单 — SPEC 15.1.

    禁用菜单不出现在当前用户菜单树中。
    """

    result = await use_case.disable_menu(ctx, menu_id)
    return MenuResponse.model_validate(result)


@router.put(
    "/menus/{menuId}/hierarchy",
    response_model=MenuResponse,
    summary="调整菜单层级与排序",
    operation_id="adjust_menu_hierarchy",
)
async def adjust_menu_hierarchy(
    request_body: MenuHierarchyRequest,
    ctx: MenuWriteCtx,
    use_case: UseCaseDep,
    menu_id: Annotated[UUID, Path(alias="menuId", description="菜单 ID")],
) -> MenuResponse:
    """调整菜单层级与排序 — SPEC 15.1.

    循环防护:
      - 直接循环（目标父菜单是自身）返回 409（``MENU.CYCLE_DETECTED``）。
      - 间接循环（目标父菜单是自身后代）返回 409（``MENU.CYCLE_DETECTED``）。
      - 并发调整通过事务级咨询锁序列化，防止竞态形成循环。

    父菜单不存在或已禁用返回 400（``MENU.INVALID_PARENT``）。
    """

    result = await use_case.adjust_hierarchy(ctx, menu_id, request_body)
    return MenuResponse.model_validate(result)


@router.delete(
    "/menus/{menuId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除菜单",
    operation_id="delete_menu",
)
async def delete_menu(
    ctx: MenuWriteCtx,
    use_case: UseCaseDep,
    menu_id: Annotated[UUID, Path(alias="menuId", description="菜单 ID")],
) -> Response:
    """删除菜单 — SPEC 15.1.

    存在子菜单返回 409（``MENU.HAS_CHILDREN``）。
    """

    await use_case.delete_menu(ctx, menu_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════════════════════════════════════════════════════
# 角色菜单分配 — /roles/{roleId}/menus 前缀
# ═══════════════════════════════════════════════════════════════════════════════


@router.put(
    "/roles/{roleId}/menus",
    response_model=RoleMenuIdsResponse,
    summary="为角色分配菜单（全量替换）",
    operation_id="assign_role_menus",
)
async def assign_role_menus(
    request_body: AssignRoleMenusRequest,
    ctx: RoleMenuWriteCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> RoleMenuIdsResponse:
    """为角色分配菜单（全量替换，幂等）— SPEC 15.1.

    SPEC 15.1: "为角色分配和移除菜单"。
    全量替换天然幂等——相同输入多次调用结果一致。
    菜单不存在返回 404（``MENU.NOT_FOUND``）。
    """

    result = await use_case.assign_role_menus(ctx, role_id, request_body)
    return RoleMenuIdsResponse.model_validate(result)


@router.delete(
    "/roles/{roleId}/menus/{menuId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移除角色单个菜单",
    operation_id="remove_role_menu",
)
async def remove_role_menu(
    ctx: RoleMenuWriteCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
    menu_id: Annotated[UUID, Path(alias="menuId", description="菜单 ID")],
) -> Response:
    """移除角色单个菜单（幂等）— SPEC 15.1.

    幂等——关联不存在时也返回 204。
    """

    await use_case.remove_role_menu(ctx, role_id, menu_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/roles/{roleId}/menus",
    response_model=RoleMenuIdsResponse,
    summary="查询角色已分配菜单",
    operation_id="get_role_menu_ids",
)
async def get_role_menu_ids(
    ctx: MenuReadCtx,
    use_case: UseCaseDep,
    role_id: Annotated[UUID, Path(alias="roleId", description="角色 ID")],
) -> RoleMenuIdsResponse:
    """查询角色已分配的菜单 ID 列表 — SPEC 15.1."""

    menu_ids = await use_case.get_role_menu_ids(ctx, role_id)
    return RoleMenuIdsResponse(role_id=role_id, menu_ids=menu_ids)


# ═══════════════════════════════════════════════════════════════════════════════
# 当前用户菜单与权限 — /me 前缀
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/me/menus",
    response_model=list[MenuTreeResponse],
    summary="当前用户菜单树",
    operation_id="get_current_user_menus",
)
async def get_current_user_menus(
    ctx: AuthCtx,
    use_case: UseCaseDep,
) -> list[MenuTreeResponse]:
    """当前用户菜单树 — SPEC 15.2.

    SPEC 15.2: "根据当前用户角色返回可访问菜单树"。
    根据当前用户启用角色聚合可访问菜单树。
    不可见菜单不出现在当前用户菜单树中（SPEC 23.5: 仅前端展示控制）。

    SPEC 15.2: "菜单变更事务提交后，当前用户下一次菜单查询立即读取新关系"。
    每次调用查库，无缓存。
    """

    tree = await use_case.get_current_user_menu_tree(ctx)
    return [MenuTreeResponse.model_validate(node) for node in tree]


@router.get(
    "/me/permissions",
    response_model=PermissionCodesResponse,
    summary="当前用户权限编码",
    operation_id="get_current_user_permissions",
)
async def get_current_user_permissions(
    ctx: AuthCtx,
    use_case: UseCaseDep,
) -> PermissionCodesResponse:
    """当前用户按钮/操作权限编码 — SPEC 15.2.

    SPEC 15.2: "返回当前用户拥有的按钮或操作权限编码"。
    返回当前用户启用角色权限并集（来自 RBAC 权限点）。
    每次调用查库，无缓存（SPEC 13.3）。
    """

    permissions = await use_case.get_current_user_permissions(ctx)
    return PermissionCodesResponse(permissions=sorted(permissions))
