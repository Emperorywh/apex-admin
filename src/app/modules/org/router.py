"""组织模块 Router — API 层（SPEC 5.2 / 9.1 / 9.2 / 9.3 / 14.1）.

SPEC 5.2: "禁止路由层直接访问数据库"。
SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository
或提交接口"。

Router 通过 FastAPI 依赖注入获得 ``OrgUseCase``。

路由组织（SPEC 9.1: 按模块分组）:
  部门管理 — ``/departments`` 前缀:
    POST   /departments                       创建部门
    GET    /departments/tree                   查询部门树
    GET    /departments/{department_id}        查询部门详情
    PUT    /departments/{department_id}        更新部门
    POST   /departments/{department_id}/enable 启用部门
    POST   /departments/{department_id}/disable 禁用部门
    PUT    /departments/{department_id}/hierarchy 调整层级与排序
    PUT    /departments/{department_id}/leader 设置部门负责人
    DELETE /departments/{department_id}        删除部门
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.application.context import UseCaseContext
from app.modules.auth.permission import require_permission
from app.modules.org.schemas import (
    DepartmentCreateRequest,
    DepartmentDetailResponse,
    DepartmentHierarchyRequest,
    DepartmentLeaderRequest,
    DepartmentResponse,
    DepartmentTreeResponse,
    DepartmentUpdateRequest,
)
from app.modules.org.use_case import OrgUseCase

router = APIRouter(tags=["org"])


# ── 依赖注入 — 组合根装配 ──────────────────────────────────────────────────


def get_org_use_case(request: Request) -> OrgUseCase:
    """构造 ``OrgUseCase`` — 组合根装配（SPEC 5.2）."""

    from app.application.ports import SystemClock, UuidGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit import adapter as _audit_adapter
    from app.modules.identity import adapter as _identity_adapter

    engine = request.app.state.db_engine

    def uow_factory() -> SqlAlchemyUnitOfWork:
        """每次调用返回新 UoW — SPEC 5.6."""

        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return _audit_adapter.SqlAlchemyAuditRepository(session)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        """从 session 构造用户认证信息 Port — SPEC 5.2 跨模块."""

        return _identity_adapter.SqlAlchemyUserAuthAdapter(session)

    return OrgUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=audit_factory,
        user_auth_port_factory=user_auth_port_factory,
    )


UseCaseDep = Annotated[OrgUseCase, Depends(get_org_use_case)]

# SPEC 23.5: 管理端通过权限依赖保护。
DeptReadCtx = Annotated[UseCaseContext, Depends(require_permission("org:dept:read"))]
DeptWriteCtx = Annotated[UseCaseContext, Depends(require_permission("org:dept:write"))]


# ═══════════════════════════════════════════════════════════════════════════════
# 部门管理 — /departments 前缀
# ═══════════════════════════════════════════════════════════════════════════════


@router.post(
    "/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建部门",
    operation_id="create_department",
)
async def create_department(
    response: Response,
    request_body: DepartmentCreateRequest,
    ctx: DeptWriteCtx,
    use_case: UseCaseDep,
) -> DepartmentResponse:
    """创建部门 — HTTP 201 + Location（SPEC 9.3 / 14.1）.

    部门编码冲突返回 409（``ORG.DEPT_ALREADY_EXISTS``）。
    父部门不存在或已禁用返回 400（``ORG.DEPT_INVALID_PARENT``）。
    """

    result = await use_case.create_department(ctx, request_body)
    response.headers["Location"] = f"/api/v1/departments/{result['id']}"
    return DepartmentResponse.model_validate(result)


@router.get(
    "/departments/tree",
    response_model=list[DepartmentTreeResponse],
    summary="查询部门树",
    operation_id="get_department_tree",
)
async def get_department_tree(
    ctx: DeptReadCtx,
    use_case: UseCaseDep,
    include_disabled: Annotated[
        bool,
        Query(description="是否包含禁用状态的部门（默认 true）"),
    ] = True,
) -> list[DepartmentTreeResponse]:
    """查询部门树 — SPEC 14.1.

    返回完整的部门树结构。默认包含禁用部门（管理员需要看到完整结构）。
    当 ``include_disabled=false`` 时，禁用部门被排除。

    SPEC 14.1: "部门禁用后树查询可见性符合文档规则"。
    """

    tree = await use_case.get_department_tree(
        ctx,
        include_disabled=include_disabled,
    )
    return [DepartmentTreeResponse.model_validate(node) for node in tree]


@router.get(
    "/departments/{department_id}",
    response_model=DepartmentDetailResponse,
    summary="查询部门详情",
    operation_id="get_department_detail",
)
async def get_department_detail(
    ctx: DeptReadCtx,
    use_case: UseCaseDep,
    department_id: Annotated[UUID, Path(description="部门 ID")],
) -> DepartmentDetailResponse:
    """查询部门详情（含子部门数量）— SPEC 14.1.

    不存在返回 404（``ORG.DEPT_NOT_FOUND``）。
    """

    result = await use_case.get_department_detail(ctx, department_id)
    return DepartmentDetailResponse.model_validate(result)


@router.put(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
    summary="更新部门",
    operation_id="update_department",
)
async def update_department(
    request_body: DepartmentUpdateRequest,
    ctx: DeptWriteCtx,
    use_case: UseCaseDep,
    department_id: Annotated[UUID, Path(description="部门 ID")],
) -> DepartmentResponse:
    """更新部门基本信息 — SPEC 14.1.

    层级调整使用独立端点 ``PUT /departments/{id}/hierarchy``。
    """

    result = await use_case.update_department(ctx, department_id, request_body)
    return DepartmentResponse.model_validate(result)


@router.post(
    "/departments/{department_id}/enable",
    response_model=DepartmentResponse,
    summary="启用部门",
    operation_id="enable_department",
)
async def enable_department(
    ctx: DeptWriteCtx,
    use_case: UseCaseDep,
    department_id: Annotated[UUID, Path(description="部门 ID")],
) -> DepartmentResponse:
    """启用部门 — SPEC 14.1."""

    result = await use_case.enable_department(ctx, department_id)
    return DepartmentResponse.model_validate(result)


@router.post(
    "/departments/{department_id}/disable",
    response_model=DepartmentResponse,
    summary="禁用部门",
    operation_id="disable_department",
)
async def disable_department(
    ctx: DeptWriteCtx,
    use_case: UseCaseDep,
    department_id: Annotated[UUID, Path(description="部门 ID")],
) -> DepartmentResponse:
    """禁用部门 — SPEC 14.1.

    SPEC 14.1: "部门禁用后树查询可见性符合文档规则"。
    禁用部门在树查询中标记为禁用但默认仍可见。
    """

    result = await use_case.disable_department(ctx, department_id)
    return DepartmentResponse.model_validate(result)


@router.put(
    "/departments/{department_id}/hierarchy",
    response_model=DepartmentResponse,
    summary="调整部门层级与排序",
    operation_id="adjust_department_hierarchy",
)
async def adjust_department_hierarchy(
    request_body: DepartmentHierarchyRequest,
    ctx: DeptWriteCtx,
    use_case: UseCaseDep,
    department_id: Annotated[UUID, Path(description="部门 ID")],
) -> DepartmentResponse:
    """调整部门层级与排序 — SPEC 14.1.

    循环防护:
      - 直接循环（目标父部门是自身）返回 409（``ORG.DEPT_CYCLE_DETECTED``）。
      - 间接循环（目标父部门是自身后代）返回 409（``ORG.DEPT_CYCLE_DETECTED``）。
      - 并发调整通过事务级咨询锁序列化，防止竞态形成循环。

    父部门不存在或已禁用返回 400（``ORG.DEPT_INVALID_PARENT``）。
    """

    result = await use_case.adjust_hierarchy(ctx, department_id, request_body)
    return DepartmentResponse.model_validate(result)


@router.put(
    "/departments/{department_id}/leader",
    response_model=DepartmentResponse,
    summary="设置部门负责人",
    operation_id="set_department_leader",
)
async def set_department_leader(
    request_body: DepartmentLeaderRequest,
    ctx: DeptWriteCtx,
    use_case: UseCaseDep,
    department_id: Annotated[UUID, Path(description="部门 ID")],
) -> DepartmentResponse:
    """设置部门负责人 — SPEC 14.1.

    负责人引用用户 ID（跨模块不建数据库外键，SPEC 5.5）。
    通过 identity 模块 Port 校验用户存在性。
    不存在返回 404（``USER.NOT_FOUND``）。
    """

    result = await use_case.set_leader(ctx, department_id, request_body)
    return DepartmentResponse.model_validate(result)


@router.delete(
    "/departments/{department_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除部门",
    operation_id="delete_department",
)
async def delete_department(
    ctx: DeptWriteCtx,
    use_case: UseCaseDep,
    department_id: Annotated[UUID, Path(description="部门 ID")],
) -> Response:
    """删除部门 — SPEC 14.1.

    SPEC 14.1: "有用户或子部门时的删除规则明确"。
    - 存在子部门返回 409（``ORG.DEPT_HAS_CHILDREN``）。
    - 存在用户关联返回 409（``ORG.DEPT_HAS_USERS``）。
    """

    await use_case.delete_department(ctx, department_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
