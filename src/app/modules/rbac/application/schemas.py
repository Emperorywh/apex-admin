"""RBAC 模块请求/响应 Schema（SPEC §9.2、§9.3、§13.2）。

继承 :class:`~app.api.schemas.BaseRequestModel` 和
:class:`~app.api.schemas.BaseResponseModel`，确保未知字段被拒绝
（``extra="forbid"``）和 snake_case 命名一致。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.api.schemas import BaseRequestModel, BaseResponseModel

# ---------------------------------------------------------------------------
# 请求 Schema
# ---------------------------------------------------------------------------


class CreateRoleRequest(BaseRequestModel):
    """创建角色请求 Schema（SPEC §9.2、§13.2）。

    Attributes:
        code: 角色编码，2–50 个字符，小写字母数字下划线连字符
        name: 角色名称，1–100 个字符
        description: 描述（可选），最长 500 个字符
        is_super_admin: 是否为超级管理员角色
    """

    code: str = Field(
        min_length=2,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="角色编码，小写字母开头，小写字母数字下划线连字符",
    )
    name: str = Field(
        min_length=1,
        max_length=100,
        description="角色名称",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="角色描述（可选）",
    )
    is_super_admin: bool = Field(
        default=False,
        description="是否为超级管理员角色（SPEC §13.4）",
    )


class UpdateRoleRequest(BaseRequestModel):
    """更新角色请求 Schema（部分更新，SPEC §9.2、§13.2）。

    不允许更新编码、状态和 is_super_admin/is_builtin 标志。

    Attributes:
        name: 角色名称（可选）
        description: 描述（可选，传 null 清空）
    """

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="角色名称（可选）",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="角色描述（可选，传 null 清空）",
    )


class AssignPermissionsRequest(BaseRequestModel):
    """为角色分配权限点请求 Schema（SPEC §9.2、§13.2）。

    全量替换语义：传入的权限集合完全替换角色现有权限。

    Attributes:
        permission_codes: 权限点编码集合
    """

    permission_codes: list[str] = Field(
        description="权限点编码集合（全量替换）",
    )


class AssignRolesRequest(BaseRequestModel):
    """为用户分配角色请求 Schema（SPEC §9.2、§13.2）。

    增量语义：在用户现有角色基础上追加指定角色。

    Attributes:
        role_codes: 角色编码集合
    """

    role_codes: list[str] = Field(
        description="角色编码集合（增量分配）",
    )


class RemoveRolesRequest(BaseRequestModel):
    """移除用户角色请求 Schema（SPEC §9.2、§13.2）。

    Attributes:
        role_codes: 角色编码集合
    """

    role_codes: list[str] = Field(
        description="要移除的角色编码集合",
    )


# ---------------------------------------------------------------------------
# 响应 Schema
# ---------------------------------------------------------------------------


class RoleResponse(BaseResponseModel):
    """角色响应 Schema（SPEC §9.3、§13.2）。

    Attributes:
        id: 角色 UUID
        code: 角色编码
        name: 角色名称
        status: 角色状态（``active`` 或 ``disabled``）
        description: 描述（可能为 null）
        is_builtin: 是否为系统内置角色
        is_super_admin: 是否为超级管理员角色
        created_at: 创建时间
        created_by: 创建人 ID（可能为 null）
        updated_at: 更新时间
        updated_by: 更新人 ID（可能为 null）
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    code: str
    name: str
    status: str
    description: str | None
    is_builtin: bool
    is_super_admin: bool
    created_at: datetime
    created_by: UUID | None
    updated_at: datetime
    updated_by: UUID | None


class PermissionListResponse(BaseResponseModel):
    """权限点编码列表响应 Schema（SPEC §9.3、§13.2）。

    Attributes:
        permission_codes: 权限点编码集合
    """

    permission_codes: list[str]


class RoleMemberListResponse(BaseResponseModel):
    """角色成员列表响应 Schema（SPEC §9.3、§13.2）。

    Attributes:
        user_ids: 成员用户 UUID 列表
    """

    user_ids: list[UUID]
