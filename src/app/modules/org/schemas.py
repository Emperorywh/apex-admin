"""组织模块请求与响应 Schema — SPEC 9.2 / 9.3 / 14.1 / 14.2 / 14.3.

SPEC 9.2 约定:
  - 创建、全量更新请求拒绝未知字段（``extra="forbid"``）。
  - 请求参数使用明确的 Schema 校验。

SPEC 9.3 约定:
  - JSON 字段统一使用 snake_case。
  - 普通成功响应直接返回资源 Schema，不使用成功信封。
  - 创建成功返回 HTTP 201 + Location。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, Field

from app.core.api.schemas import StrictBaseModel

# ── 部门请求 Schema ──────────────────────────────────────────────────────


class DepartmentCreateRequest(StrictBaseModel):
    """创建部门请求 — SPEC 9.2 / 14.1.

    属性:
        code:         部门编码，2-100 字符，小写字母/数字/下划线。
        display_name: 显示名称，1-200 字符。
        description:  描述（可选），最多 500 字符。
        parent_id:    父部门 ID（可选，根部门为 null）。
        sort_order:   排序序号，默认 0。
        leader_id:    负责人用户 ID（可选）。
    """

    code: str = Field(
        min_length=2,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="部门编码（小写字母、数字和下划线，字母开头）",
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
    parent_id: UUID | None = Field(
        default=None,
        description="父部门 ID（根部门为 null）",
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="排序序号",
    )
    leader_id: UUID | None = Field(
        default=None,
        description="负责人用户 ID（可选）",
    )


class DepartmentUpdateRequest(StrictBaseModel):
    """更新部门请求 — SPEC 9.2 / 14.1.

    部门编码不可变更——编码是全局唯一的稳定标识。
    层级调整使用独立端点（``PUT /departments/{id}/hierarchy``）。

    属性:
        display_name: 显示名称，1-200 字符。
        description:  描述（可选），最多 500 字符。
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


class DepartmentHierarchyRequest(StrictBaseModel):
    """调整部门层级与排序请求 — SPEC 14.1.

    用于调整部门的父级和排序序号。循环防护在 Use Case 中执行。

    属性:
        parent_id:  新的父部门 ID（null 表示设为根部门）。
        sort_order: 排序序号。
    """

    parent_id: UUID | None = Field(
        description="新的父部门 ID（null 表示设为根部门）",
    )
    sort_order: int = Field(
        default=0,
        ge=0,
        description="排序序号",
    )


class DepartmentLeaderRequest(StrictBaseModel):
    """设置部门负责人请求 — SPEC 14.1.

    负责人引用用户 ID，跨模块不建数据库外键（SPEC 5.5）。
    设为 null 清除负责人。

    属性:
        leader_id: 负责人用户 ID（null 清除负责人）。
    """

    leader_id: UUID | None = Field(
        description="负责人用户 ID（null 清除负责人）",
    )


# ── 响应 Schema ──────────────────────────────────────────────────────────


class DepartmentResponse(BaseModel):
    """部门响应模型 — SPEC 9.3 / 14.1."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    parent_id: UUID | None
    status: str
    sort_order: int
    leader_id: UUID | None
    created_at: datetime
    updated_at: datetime


class DepartmentDetailResponse(BaseModel):
    """部门详情响应（含子部门数量）— SPEC 9.3 / 14.1."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    parent_id: UUID | None
    status: str
    sort_order: int
    leader_id: UUID | None
    child_count: int
    created_at: datetime
    updated_at: datetime


class DepartmentTreeResponse(BaseModel):
    """部门树节点响应 — SPEC 9.3 / 14.1."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    parent_id: UUID | None
    status: str
    sort_order: int
    leader_id: UUID | None
    children: list[DepartmentTreeResponse]
    created_at: datetime
    updated_at: datetime


# ── 岗位请求 Schema — SPEC 14.2 ────────────────────────────────────────────


class PostCreateRequest(StrictBaseModel):
    """创建岗位请求 — SPEC 9.2 / 14.2.

    SPEC 14.2: "岗位不直接替代角色和权限"。

    属性:
        code:         岗位编码，2-100 字符，小写字母/数字/下划线。
        display_name: 显示名称，1-200 字符。
        description:  描述（可选），最多 500 字符。
        sort_order:   排序序号，默认 0。
    """

    code: str = Field(
        min_length=2,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="岗位编码（小写字母、数字和下划线，字母开头）",
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


class PostUpdateRequest(StrictBaseModel):
    """更新岗位请求 — SPEC 9.2 / 14.2.

    岗位编码不可变更——编码是全局唯一的稳定标识。

    属性:
        display_name: 显示名称，1-200 字符。
        description:  描述（可选），最多 500 字符。
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


# ── 用户组织关系请求 Schema — SPEC 14.3 ────────────────────────────────────


class AssignUserDepartmentRequest(StrictBaseModel):
    """为用户分配主部门请求 — SPEC 14.3.

    属性:
        department_id: 部门 ID。
    """

    department_id: UUID = Field(description="部门 ID")


class AssignUserPostRequest(StrictBaseModel):
    """为用户分配岗位请求 — SPEC 14.2.

    属性:
        post_id: 岗位 ID。
    """

    post_id: UUID = Field(description="岗位 ID")


# ── 岗位与关系响应 Schema ──────────────────────────────────────────────────


class PostResponse(BaseModel):
    """岗位响应模型 — SPEC 9.3 / 14.2."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    status: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


class PostDetailResponse(BaseModel):
    """岗位详情响应（含关联用户数量）— SPEC 9.3 / 14.2."""

    model_config = {"extra": "forbid"}

    id: UUID
    code: str
    display_name: str
    description: str | None
    status: str
    sort_order: int
    user_count: int
    created_at: datetime
    updated_at: datetime


class UserDepartmentResponse(BaseModel):
    """用户部门关系响应 — SPEC 14.3."""

    model_config = {"extra": "forbid"}

    department_id: UUID
    department_code: str
    department_name: str
    is_primary: bool


class UserPostResponse(BaseModel):
    """用户岗位关系响应 — SPEC 14.2."""

    model_config = {"extra": "forbid"}

    post_id: UUID
    post_code: str
    post_name: str
