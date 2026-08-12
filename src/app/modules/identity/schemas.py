"""用户模块请求与响应 Schema — SPEC 9.2 / 9.3 / 11.1 / 11.2 / 23.2.

SPEC 9.2 约定:
  - 创建、全量更新和部分更新请求统一拒绝未知字段（``extra="forbid"``）。
  - 请求参数使用明确的 Schema 校验。
  - 创建、更新、查询和响应模型按职责区分。

SPEC 9.3 约定:
  - JSON 字段统一使用 camelCase。
  - 时间字段统一为带时区的 ISO 8601 字符串。
  - 普通成功响应直接返回资源 Schema，不使用成功信封。
  - 敏感字段不得进入响应模型。

SPEC 23.2 密码策略:
  - 密码最小长度为 12 个 Unicode 字符，最大长度为 128 个 Unicode 字符。

SPEC 11.1 自助端点白名单:
  - 自助资料更新仅允许 display_name、phone、email（非 username、status）。
  - 自助改密必须提供 old_password 和 new_password。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import Field

from app.core.api.schemas import ApiModel, StrictBaseModel
from app.core.security.password import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

# ── 管理端请求 Schema ──────────────────────────────────────────────────────


class UserCreateRequest(StrictBaseModel):
    """创建用户请求 — SPEC 9.2 / 11.1 / 23.2.

    继承 ``StrictBaseModel``（``extra="forbid"``），携带未知字段的请求返回 422。

    属性:
        username:     用户名，3-100 字符。
        display_name: 显示名称，1-200 字符。
        password:     初始明文密码，12-128 Unicode 字符（SPEC 23.2）。
        phone:        手机号（可选），最多 50 字符。
        email:        邮箱（可选），最多 255 字符，格式校验。
    """

    username: str = Field(
        min_length=3,
        max_length=100,
        description="用户名/登录账号",
    )
    display_name: str = Field(
        min_length=1,
        max_length=200,
        description="显示名称",
    )
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="初始密码（12-128 Unicode 字符，SPEC 23.2）",
    )
    phone: str | None = Field(
        default=None,
        max_length=50,
        description="手机号（可选）",
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="邮箱（可选）",
    )


class UserUpdateRequest(StrictBaseModel):
    """更新用户资料请求（管理端全量更新）— SPEC 9.2 / 11.1.

    管理端可更新显示名称、手机号和邮箱。
    username 不可变更——用户名是全局唯一的稳定登录账号标识，
    变更会影响历史审计记录可追溯性（SPEC 11.3: "用户名称发生变化时，
    历史审计记录仍能识别当时操作者"——通过显示名快照解决，
    username 本身保持不变）。

    属性:
        display_name: 显示名称，1-200 字符。
        phone:        手机号（可选），最多 50 字符。
        email:        邮箱（可选），最多 255 字符，格式校验。
    """

    display_name: str = Field(
        min_length=1,
        max_length=200,
        description="显示名称",
    )
    phone: str | None = Field(
        default=None,
        max_length=50,
        description="手机号（可选）",
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="邮箱（可选）",
    )


class UserResetPasswordRequest(StrictBaseModel):
    """管理员重置密码请求 — SPEC 11.1.

    管理员设置新密码，不需要旧密码（SPEC 11.1: "重置用户密码"）。
    重置后发布 ``USER.PASSWORD_RESET_BY_ADMIN`` 事件，
    auth 模块（TASK-013）吊销该用户全部会话。

    属性:
        new_password: 新明文密码，12-128 Unicode 字符（SPEC 23.2）。
    """

    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="新密码（12-128 Unicode 字符，SPEC 23.2）",
    )


# ── 自助端点请求 Schema ────────────────────────────────────────────────────


class SelfProfileUpdateRequest(StrictBaseModel):
    """自助更新资料请求 — SPEC 11.1 / 9.2.

    SPEC 11.1: "用户更新允许自助修改的资料"。
    自助端点仅允许白名单字段：display_name、phone、email。
    username、status 等字段不在白名单内。

    继承 ``StrictBaseModel``（``extra="forbid"``），携带未知字段
    （如 username、status）的请求返回 422（SPEC 9.2）。

    属性:
        display_name: 显示名称，1-200 字符。
        phone:        手机号（可选），最多 50 字符。
        email:        邮箱（可选），最多 255 字符，格式校验。
    """

    display_name: str = Field(
        min_length=1,
        max_length=200,
        description="显示名称",
    )
    phone: str | None = Field(
        default=None,
        max_length=50,
        description="手机号（可选）",
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="邮箱（可选）",
    )


class SelfChangePasswordRequest(StrictBaseModel):
    """自助修改密码请求 — SPEC 11.1.

    SPEC 11.1: "用户修改自己的密码"。
    自助改密必须提供旧密码（SPEC: 自助改密必须校验旧密码），
    与管理员重置密码不同——管理员重置不需要旧密码。

    属性:
        old_password: 旧明文密码（用于校验）。
        new_password: 新明文密码，12-128 Unicode 字符（SPEC 23.2）。
    """

    old_password: str = Field(
        min_length=1,
        description="旧密码（需校验）",
    )
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="新密码（12-128 Unicode 字符，SPEC 23.2）",
    )


# ── 响应 Schema ────────────────────────────────────────────────────────────


class UserDepartmentInfo(ApiModel):
    """用户所属部门信息 — SPEC 11.1 / 14.3.

    由 org 模块公开 Port 聚合的部门投影经 Pydantic 校验后嵌入
    ``UserResponse``，JSON 键序列化为 camelCase（SPEC 9.3）。
    """

    model_config = {"extra": "forbid"}

    department_id: UUID
    department_code: str
    department_name: str
    is_primary: bool


class UserPostInfo(ApiModel):
    """用户岗位信息 — SPEC 11.1 / 14.2.

    由 org 模块公开 Port 聚合的岗位投影经 Pydantic 校验后嵌入
    ``UserResponse``，JSON 键序列化为 camelCase（SPEC 9.3）。
    """

    model_config = {"extra": "forbid"}

    post_id: UUID
    post_code: str
    post_name: str


class UserResponse(ApiModel):
    """用户响应模型 — SPEC 9.3 / 11.1 / 11.2.

    SPEC 9.3: "普通成功响应直接返回资源 Schema，不使用成功信封"。
    SPEC 9.3: "敏感字段不得进入响应模型"。
    密码哈希不出现在响应中（SPEC 23.2: "禁止记录和回显密码"）。

    SPEC 11.1: "通过 G3 后同时返回部门和岗位关系"。
    department 和 posts 由 org 模块公开 Port 聚合返回（SPEC 14.3）。
    G2 阶段（org 模块未接入时）department 为 None、posts 为空列表。

    JSON 字段使用 camelCase（继承自 ``ApiModel``），
    时间为带时区 ISO 8601 字符串。
    """

    model_config = {"extra": "forbid"}

    id: UUID
    username: str
    display_name: str
    status: str
    phone: str | None
    email: str | None
    last_login_at: datetime | None
    password_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    department: UserDepartmentInfo | None = None
    posts: list[UserPostInfo] = []
