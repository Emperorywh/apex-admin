"""RBAC 模块请求与响应 Schema — SPEC 9.2 / 9.3 / 13.2.

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

# ── 角色请求 Schema ──────────────────────────────────────────────────────


class RoleCreateRequest(StrictBaseModel):
    """创建角色请求 — SPEC 9.2 / 13.2.

    属性:
        code:         角色编码，2-100 字符，小写字母/数字/下划线。
        display_name: 显示名称，1-200 字符。
        description:  描述（可选），最多 500 字符。
        sort_order:   排序序号，默认 0。
    """

    code: str = Field(
        min_length=2,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="角色编码（小写字母、数字和下划线，字母开头）",
    )
    display_name: str = Field(
        min_length=1,
        max_length=200,
        description="显示名称",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="描述（可选）",
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="排序序号",
    )


class RoleUpdateRequest(StrictBaseModel):
    """更新角色请求 — SPEC 9.2 / 13.2.

    角色编码不可变更——编码是全局唯一的稳定标识。

    属性:
        display_name: 显示名称，1-200 字符。
        description:  描述（可选），最多 500 字符。
        sort_order:   排序序号。
    """

    display_name: str = Field(
        min_length=1,
        max_length=200,
        description="显示名称",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="描述（可选）",
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="排序序号",
    )


class AssignPermissionsRequest(StrictBaseModel):
    """为角色分配权限点请求 — SPEC 13.2.

    全量替换角色的权限点集合。

    属性:
        permission_codes: 权限编码列表（全量替换）。空列表表示清除全部权限。
    """

    permission_codes: list[str] = Field(
        description="权限编码列表（全量替换）",
    )


class AssignUserRolesRequest(StrictBaseModel):
    """为用户分配角色请求 — SPEC 13.2.

    全量替换用户的角色集合。

    属性:
        role_codes: 角色编码列表（全量替换）。
    """

    role_codes: list[str] = Field(
        description="角色编码列表（全量替换）",
    )


# ── 响应 Schema ──────────────────────────────────────────────────────────


class RoleResponse(ApiModel):
    """角色响应模型 — SPEC 9.3 / 13.2."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    status: str
    is_builtin: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class RoleDetailResponse(ApiModel):
    """角色详情响应（含权限编码列表）— SPEC 9.3 / 13.2."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    status: str
    is_builtin: bool
    sort_order: int
    permission_codes: list[str]
    member_count: int
    created_at: datetime
    updated_at: datetime


class RoleMemberResponse(ApiModel):
    """角色成员响应 — SPEC 13.2."""

    model_config = {"extra": "forbid"}

    user_id: UUID
    role_id: UUID
    created_at: datetime
    created_by: str | None


class PermissionResponse(ApiModel):
    """权限点响应模型 — SPEC 9.3 / 13.1."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    module_code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
