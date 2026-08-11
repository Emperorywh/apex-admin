"""组织模块领域实体与状态枚举 — SPEC 14.1 / 14.2 / 14.3 / 5.2.

SPEC 14.1 部门管理:
  - 部门为树形实体，具有父子层级和排序。
  - 负责人引用用户 ID（跨模块不建数据库外键，存稳定 ID）。
  - 启用和禁用状态控制树查询可见性。

SPEC 14.2 岗位管理:
  - 岗位具有编码、名称、状态。
  - 岗位不直接替代角色和权限（SPEC 14.2）。

SPEC 14.3 用户组织关系:
  - 用户具有明确的主部门。
  - 用户可被分配多个岗位。

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


class DepartmentStatus(StrEnum):
    """部门状态枚举 — 稳定字符串编码（SPEC 8.3 / 14.1）.

    SPEC 14.1: "启用和禁用部门"。
    禁用部门在树查询中标记为禁用但默认仍可见（管理员需要看到完整结构）。

    属性:
        ACTIVE:   启用状态——部门有效。
        DISABLED: 禁用状态——部门失效，树查询中标记禁用。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Department:
    """部门领域实体 — SPEC 14.1.

    SPEC 14.1 部门管理:
      - 创建部门、查询部门树、查询部门详情。
      - 更新部门、启用和禁用部门。
      - 调整部门层级和排序。
      - 设置部门负责人。
      - 防止形成循环层级。

    SPEC 5.5: 跨模块数据库外键默认禁止。``leader_id`` 引用 identity 模块
    ``users`` 表，不做数据库外键约束，通过应用层 Port 校验用户存在性。

    属性:
        id:           全局唯一标识（UUID）。
        code:         部门编码（全局唯一，稳定标识）。
        display_name: 显示名称。
        description:  描述（可为空）。
        parent_id:    父部门 ID（可为空，根部门无父级）。
        status:       部门状态（ACTIVE / DISABLED）。
        sort_order:   排序序号（同级部门排序）。
        leader_id:    负责人用户 ID（引用 identity 模块，无数据库外键，可为空）。
        created_at:   创建时间（UTC）。
        updated_at:   更新时间（UTC）。
        created_by:   创建人标识（可为空）。
        updated_by:   更新人标识（可为空）。
    """

    id: UUID
    code: str
    display_name: str
    description: str | None
    parent_id: UUID | None
    status: DepartmentStatus
    sort_order: int
    leader_id: UUID | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


@dataclass(frozen=True)
class DepartmentTreeNode:
    """部门树节点 — SPEC 14.1.

    用于查询部门树时的层级结构表示。每个节点包含部门基本信息
    和子节点列表。

    属性:
        id:           部门 ID。
        code:         部门编码。
        display_name: 显示名称。
        description:  描述。
        parent_id:    父部门 ID。
        status:       部门状态。
        sort_order:   排序序号。
        leader_id:    负责人用户 ID。
        created_at:   创建时间。
        updated_at:   更新时间。
        children:     子节点列表（已按 sort_order 排序）。
    """

    id: UUID
    code: str
    display_name: str
    description: str | None
    parent_id: UUID | None
    status: DepartmentStatus
    sort_order: int
    leader_id: UUID | None
    created_at: datetime
    updated_at: datetime
    children: list[DepartmentTreeNode] = field(default_factory=list)


class PostStatus(StrEnum):
    """岗位状态枚举 — 稳定字符串编码（SPEC 8.3 / 14.2）.

    SPEC 14.2: "启用和禁用岗位"。

    属性:
        ACTIVE:   启用状态——岗位有效，可分配给用户。
        DISABLED: 禁用状态——岗位失效，不可分配给新用户。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Post:
    """岗位领域实体 — SPEC 14.2.

    SPEC 14.2: "岗位不直接替代角色和权限"——岗位仅为组织管理维度。

    属性:
        id:           全局唯一标识（UUID）。
        code:         岗位编码（全局唯一，稳定标识）。
        display_name: 显示名称。
        description:  描述（可为空）。
        status:       岗位状态（ACTIVE / DISABLED）。
        sort_order:   排序序号。
        created_at:   创建时间（UTC）。
        updated_at:   更新时间（UTC）。
        created_by:   创建人标识（可为空）。
        updated_by:   更新人标识（可为空）。
    """

    id: UUID
    code: str
    display_name: str
    description: str | None
    status: PostStatus
    sort_order: int
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


@dataclass(frozen=True)
class UserDepartmentInfo:
    """用户部门关系投影 — 跨模块返回（SPEC 14.3 / 5.5）.

    SPEC 5.5: 跨模块 Port 返回投影，不暴露 ORM 模型。
    供 identity 模块在用户详情中聚合返回部门关系。

    属性:
        department_id: 部门 ID。
        department_code: 部门编码。
        department_name: 部门显示名称。
        is_primary: 是否主部门。
    """

    department_id: UUID
    department_code: str
    department_name: str
    is_primary: bool


@dataclass(frozen=True)
class UserPostInfo:
    """用户岗位关系投影 — 跨模块返回（SPEC 14.2 / 14.3 / 5.5）.

    SPEC 5.5: 跨模块 Port 返回投影，不暴露 ORM 模型。
    供 identity 模块在用户详情中聚合返回岗位关系。

    属性:
        post_id:   岗位 ID。
        post_code: 岗位编码。
        post_name: 岗位显示名称。
    """

    post_id: UUID
    post_code: str
    post_name: str
