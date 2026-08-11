"""菜单模块领域实体与状态枚举 — SPEC 15.1 / 15.2 / 5.2.

SPEC 15.1 菜单资源:
  - 菜单为树形实体，通过 ``parent_id`` 自引用实现父子层级。
  - 支持目录、页面和外链三种明确类型。
  - 支持前端路由名称、路径、组件标识和图标等元数据。
  - 支持菜单可见性配置。
  - 防止形成循环层级。

SPEC 15.2 当前用户菜单:
  - 根据当前用户角色返回可访问菜单树。
  - 菜单可见性不替代服务端权限校验。
  - 菜单变更无缓存，提交即生效。

SPEC 23.5 / 13.3: 菜单可见性仅服务前端展示，授权以服务端权限校验为准。

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2: "领域规则不得依赖 FastAPI、ORM、HTTP 或具体存储 SDK"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class MenuType(StrEnum):
    """菜单类型枚举 — SPEC 15.1.

    SPEC 15.1: "支持目录、页面和外链等明确类型"。

    属性:
        DIRECTORY: 目录——用于组织菜单层级，不对应实际页面。
        PAGE:      页面——对应前端路由组件。
        LINK:      外链——外部 URL，不在应用内渲染。
    """

    DIRECTORY = "directory"
    PAGE = "page"
    LINK = "link"


class MenuStatus(StrEnum):
    """菜单状态枚举 — SPEC 15.1.

    SPEC 15.1: "启用和禁用菜单"。
    禁用菜单不出现在当前用户菜单树中。

    属性:
        ACTIVE:   启用状态——菜单有效。
        DISABLED: 禁用状态——菜单失效。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Menu:
    """菜单领域实体 — SPEC 15.1.

    SPEC 15.1 菜单资源:
      - 创建/查询/更新/启用禁用/层级排序调整。
      - 支持目录/页面/外链类型。
      - 支持前端路由元数据（路由名、路径、组件、图标）。
      - 支持可见性配置。
      - 防止循环层级。

    SPEC 23.5: ``visible`` 仅控制前端展示，不承担授权职责。
    接口访问授权由服务端 RBAC 权限校验决定（SPEC 13.3）。

    属性:
        id:          全局唯一标识（UUID）。
        parent_id:   父菜单 ID（可为空，根菜单无父级）。
        menu_type:   菜单类型（DIRECTORY / PAGE / LINK）。
        title:       显示标题。
        name:        前端路由名称（页面/外链适用，目录可为空）。
        path:        前端路由路径（页面/外链适用）。
        component:   前端组件标识（页面适用）。
        icon:        图标标识（可选）。
        sort_order:  排序序号（同级菜单排序）。
        visible:     是否可见（仅控制前端展示，SPEC 23.5）。
        status:      菜单状态（ACTIVE / DISABLED）。
        created_at:  创建时间（UTC）。
        updated_at:  更新时间（UTC）。
        created_by:  创建人标识（可为空）。
        updated_by:  更新人标识（可为空）。
    """

    id: UUID
    parent_id: UUID | None
    menu_type: MenuType
    title: str
    name: str | None
    path: str | None
    component: str | None
    icon: str | None
    sort_order: int
    visible: bool
    status: MenuStatus
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


@dataclass(frozen=True)
class MenuTreeNode:
    """菜单树节点 — SPEC 15.1 / 15.2.

    用于查询菜单树时的层级结构表示。每个节点包含菜单基本信息
    和子节点列表。

    属性:
        id:          菜单 ID。
        parent_id:   父菜单 ID。
        menu_type:   菜单类型。
        title:       显示标题。
        name:        前端路由名称。
        path:        前端路由路径。
        component:   前端组件标识。
        icon:        图标标识。
        sort_order:  排序序号。
        visible:     是否可见。
        status:      菜单状态。
        created_at:  创建时间。
        updated_at:  更新时间。
        children:    子节点列表（已按 sort_order 排序）。
    """

    id: UUID
    parent_id: UUID | None
    menu_type: MenuType
    title: str
    name: str | None
    path: str | None
    component: str | None
    icon: str | None
    sort_order: int
    visible: bool
    status: MenuStatus
    created_at: datetime
    updated_at: datetime
    children: list[MenuTreeNode] = field(default_factory=list)
