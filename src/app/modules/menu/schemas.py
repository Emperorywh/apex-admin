"""菜单模块请求与响应 Schema — SPEC 9.2 / 9.3 / 15.1 / 15.2.

SPEC 9.2 约定:
  - 创建、全量更新请求拒绝未知字段（``extra="forbid"``）。
  - 请求参数使用明确的 Schema 校验。

SPEC 9.3 约定:
  - JSON 字段统一使用 camelCase。
  - 普通成功响应直接返回资源 Schema，不使用成功信封。
  - 创建成功返回 HTTP 201 + Location。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import Field

from app.core.api.schemas import ApiModel, StrictBaseModel

# ── 菜单请求 Schema ──────────────────────────────────────────────────────


class MenuCreateRequest(StrictBaseModel):
    """创建菜单请求 — SPEC 9.2 / 15.1.

    属性:
        parent_id:   父菜单 ID（可选，根菜单为 null）。
        menu_type:   菜单类型（directory / page / link）。
        title:       显示标题，1-200 字符。
        name:        前端路由名称（可选）。
        path:        前端路由路径（可选）。
        component:   前端组件标识（可选）。
        icon:        图标标识（可选）。
        sort_order:  排序序号，默认 0。
        visible:     是否可见，默认 true。
    """

    parent_id: UUID | None = Field(
        default=None,
        description="父菜单 ID（根菜单为 null）",
    )
    menu_type: str = Field(
        description="菜单类型（directory / page / link）",
        pattern=r"^(directory|page|link)$",
    )
    title: str = Field(
        min_length=1,
        max_length=200,
        description="显示标题",
    )
    name: str | None = Field(
        default=None,
        max_length=200,
        description="前端路由名称（可选）",
    )
    path: str | None = Field(
        default=None,
        max_length=500,
        description="前端路由路径（可选）",
    )
    component: str | None = Field(
        default=None,
        max_length=500,
        description="前端组件标识（可选）",
    )
    icon: str | None = Field(
        default=None,
        max_length=200,
        description="图标标识（可选）",
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="排序序号",
    )
    visible: bool = Field(
        default=True,
        description="是否可见（仅控制前端展示，不承担授权）",
    )


class MenuUpdateRequest(StrictBaseModel):
    """更新菜单请求 — SPEC 9.2 / 15.1.

    层级调整使用独立端点（``PUT /menus/{id}/hierarchy``）。
    菜单类型不可变更。

    属性:
        title:      显示标题，1-200 字符。
        name:       前端路由名称（可选）。
        path:       前端路由路径（可选）。
        component:  前端组件标识（可选）。
        icon:       图标标识（可选）。
        visible:    是否可见。
    """

    title: str = Field(
        min_length=1,
        max_length=200,
        description="显示标题",
    )
    name: str | None = Field(
        default=None,
        max_length=200,
        description="前端路由名称（可选）",
    )
    path: str | None = Field(
        default=None,
        max_length=500,
        description="前端路由路径（可选）",
    )
    component: str | None = Field(
        default=None,
        max_length=500,
        description="前端组件标识（可选）",
    )
    icon: str | None = Field(
        default=None,
        max_length=200,
        description="图标标识（可选）",
    )
    visible: bool = Field(
        default=True,
        description="是否可见（仅控制前端展示，不承担授权）",
    )


class MenuHierarchyRequest(StrictBaseModel):
    """调整菜单层级与排序请求 — SPEC 15.1.

    用于调整菜单的父级和排序序号。循环防护在 Use Case 中执行。

    属性:
        parent_id:  新的父菜单 ID（null 表示设为根菜单）。
        sort_order: 排序序号。
    """

    parent_id: UUID | None = Field(
        description="新的父菜单 ID（null 表示设为根菜单）",
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="排序序号",
    )


# ── 角色菜单请求 Schema ──────────────────────────────────────────────────


class AssignRoleMenusRequest(StrictBaseModel):
    """为角色分配菜单请求 — SPEC 15.1.

    全量替换角色菜单。调用幂等——相同输入多次调用结果一致。

    属性:
        menu_ids: 菜单 ID 列表（全量替换）。
    """

    menu_ids: list[UUID] = Field(
        description="菜单 ID 列表（全量替换角色的菜单分配）",
    )


# ── 响应 Schema ──────────────────────────────────────────────────────────


class MenuResponse(ApiModel):
    """菜单响应模型 — SPEC 9.3 / 15.1."""

    model_config = {"extra": "forbid"}

    id: UUID
    parent_id: UUID | None
    menu_type: str
    title: str
    name: str | None
    path: str | None
    component: str | None
    icon: str | None
    sort_order: int
    visible: bool
    status: str
    created_at: datetime
    updated_at: datetime


class MenuTreeResponse(ApiModel):
    """菜单树节点响应 — SPEC 9.3 / 15.1 / 15.2.

    SPEC 23.5: ``visible`` 仅控制前端展示。当前用户菜单树端点不返回
    不可见菜单；管理端菜单树端点返回全部菜单（含不可见菜单）。
    """

    model_config = {"extra": "forbid"}

    id: UUID
    parent_id: UUID | None
    menu_type: str
    title: str
    name: str | None
    path: str | None
    component: str | None
    icon: str | None
    sort_order: int
    visible: bool
    status: str
    children: list[MenuTreeResponse]
    created_at: datetime
    updated_at: datetime


class RoleMenuIdsResponse(ApiModel):
    """角色菜单 ID 列表响应 — SPEC 15.1."""

    model_config = {"extra": "forbid"}

    role_id: UUID
    menu_ids: list[UUID]


class PermissionCodesResponse(ApiModel):
    """当前用户权限编码响应 — SPEC 15.2."""

    model_config = {"extra": "forbid"}

    permissions: list[str]
