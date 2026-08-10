"""RBAC 领域实体与状态枚举 — SPEC 13.1 / 5.2.

SPEC 13.1 RBAC 模型:
  - 用户、角色、权限点。
  - 用户与角色关系。
  - 角色与权限点关系。
  - 权限点使用稳定编码，如 ``system:user:read``。

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2: "领域规则不得依赖 FastAPI、ORM、HTTP 或具体存储 SDK"）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class RoleStatus(StrEnum):
    """角色状态枚举 — 稳定字符串编码（SPEC 8.3 / 13.2）.

    SPEC 13.2: "启用和禁用角色"。
    禁用角色的权限不再计入用户有效权限（SPEC 13.1 / 13.2）。

    属性:
        ACTIVE:   启用状态——角色有效，权限计入用户有效权限集。
        DISABLED: 禁用状态——角色失效，权限不计入用户有效权限集。
    """

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Role:
    """角色领域实体 — SPEC 13.1 / 13.2.

    SPEC 13.2 角色管理:
      - 创建角色、查询角色详情、分页查询、更新角色。
      - 启用和禁用角色。
      - 为角色分配权限点。
      - 查询角色成员。
      - 系统内置角色具有明确保护规则。

    属性:
        id:           全局唯一标识（UUID）。
        code:         角色编码（全局唯一，稳定标识）。
        display_name: 显示名称。
        description:  描述（可为空）。
        status:       角色状态（ACTIVE / DISABLED）。
        is_builtin:   是否系统内置角色——内置角色不可删除或禁用。
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
    status: RoleStatus
    is_builtin: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


@dataclass(frozen=True)
class Permission:
    """权限点领域实体 — SPEC 13.1 / 25.2.

    SPEC 13.1: 权限点使用稳定编码，表达资源和操作（如 ``system:user:read``）。
    SPEC 25.2: 权限点来自各模块 ``ModuleDefinition`` 声明，通过 ``sync-permissions``
    命令幂等同步为目录。

    属性:
        id:           全局唯一标识（UUID）。
        code:         权限编码（小写多段，全局唯一，如 ``system:user:read``）。
        display_name: 显示名称。
        description:  描述（可为空）。
        module_code:  声明此权限点的模块编码。
        is_active:    是否启用。
        created_at:   创建时间（UTC）。
        updated_at:   更新时间（UTC）。
    """

    id: UUID
    code: str
    display_name: str
    description: str | None
    module_code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RoleAssignment:
    """用户角色分配记录 — SPEC 13.1 / 13.2.

    SPEC 13.1: "用户与角色关系"。
    SPEC 13.2: "为用户分配角色"、"移除用户角色"、"查询角色成员"。

    SPEC 5.5: 跨模块数据库外键默认禁止。``user_id`` 不做外键约束，
    通过应用层 Port 校验用户存在性。

    属性:
        user_id:    用户 ID（引用 identity 模块 ``users`` 表，无数据库外键）。
        role_id:    角色 ID。
        created_at: 分配时间（UTC）。
        created_by: 分配人标识（可为空）。
    """

    user_id: UUID
    role_id: UUID
    created_at: datetime
    created_by: str | None
