"""用户模块请求/响应 Schema（SPEC §9.2、§9.3、§11.2、§23.2）。

继承 :class:`~app.api.schemas.BaseRequestModel` 和
:class:`~app.api.schemas.BaseResponseModel`，确保未知字段被拒绝
（``extra="forbid"``）和 snake_case 命名一致。

敏感字段（密码哈希）绝不出现在响应 Schema 中（SPEC §9.3、§23.2）。
密码字段使用 ``min_length`` / ``max_length`` 约束以 Unicode 字符计数，
拒绝超长密码而非静默截断（SPEC §23.2）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.api.schemas import BaseRequestModel, BaseResponseModel

# ---------------------------------------------------------------------------
# 请求 Schema（SPEC §9.2：extra="forbid"）
# ---------------------------------------------------------------------------


class CreateUserRequest(BaseRequestModel):
    """创建用户请求 Schema（SPEC §9.2、§23.2）。

    密码长度以 Unicode 字符计数：最小 12，最大 128。
    超长密码直接拒绝（不截断），符合 SPEC §23.2 要求。

    Attributes:
        username: 用户名，3–50 个字符，字母数字下划线连字符
        display_name: 显示名称，1–100 个字符
        password: 明文密码，12–128 个 Unicode 字符
        phone: 手机号（可选），最长 20 个字符
        email: 邮箱（可选），最长 255 个字符
    """

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="用户名，3–50 个字符，字母数字下划线连字符",
    )
    display_name: str = Field(
        min_length=1,
        max_length=100,
        description="显示名称",
    )
    password: str = Field(
        min_length=12,
        max_length=128,
        description="明文密码，12–128 个 Unicode 字符",
    )
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="手机号（可选）",
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="邮箱（可选）",
    )


class UpdateUserRequest(BaseRequestModel):
    """管理员更新用户资料请求 Schema（部分更新，SPEC §9.2、§11.1）。

    所有字段可选。未提供的字段保持不变；显式传 ``null`` 清空字段值。
    不允许修改用户名、状态和密码（密码通过专用端点操作）。

    Attributes:
        display_name: 显示名称（可选）
        phone: 手机号（可选，传 null 清空）
        email: 邮箱（可选，传 null 清空）
    """

    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="显示名称（可选）",
    )
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="手机号（可选，传 null 清空）",
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="邮箱（可选，传 null 清空）",
    )


class UpdateSelfProfileRequest(BaseRequestModel):
    """用户自助更新资料请求 Schema（部分更新，SPEC §9.2、§11.1）。

    与 :class:`UpdateUserRequest` 字段相同，但语义为自助操作。
    仅允许修改显示名称、手机号和邮箱。

    Attributes:
        display_name: 显示名称（可选）
        phone: 手机号（可选，传 null 清空）
        email: 邮箱（可选，传 null 清空）
    """

    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="显示名称（可选）",
    )
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="手机号（可选，传 null 清空）",
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="邮箱（可选，传 null 清空）",
    )


class ResetPasswordRequest(BaseRequestModel):
    """管理员重置密码请求 Schema（SPEC §11.1、§23.2）。

    新密码长度以 Unicode 字符计数：12–128。

    Attributes:
        new_password: 新明文密码，12–128 个 Unicode 字符
    """

    new_password: str = Field(
        min_length=12,
        max_length=128,
        description="新明文密码，12–128 个 Unicode 字符",
    )


class ChangePasswordRequest(BaseRequestModel):
    """用户自助修改密码请求 Schema（SPEC §11.1、§23.2）。

    需提供当前密码和新密码。新密码长度以 Unicode 字符计数：12–128。

    Attributes:
        current_password: 当前明文密码
        new_password: 新明文密码，12–128 个 Unicode 字符
    """

    current_password: str = Field(
        min_length=1,
        description="当前密码",
    )
    new_password: str = Field(
        min_length=12,
        max_length=128,
        description="新明文密码，12–128 个 Unicode 字符",
    )


# ---------------------------------------------------------------------------
# 响应 Schema（SPEC §9.3：不含敏感字段）
# ---------------------------------------------------------------------------


class UserResponse(BaseResponseModel):
    """用户响应 Schema（SPEC §9.3、§11.2）。

    响应直接返回资源 Schema，不使用 ``{code, message, data}`` 信封。
    时间字段使用带时区的 ISO 8601 字符串（SPEC §9.3、§6.3）。

    **密码哈希等敏感字段绝不出现在此 Schema 中**
    （SPEC §9.3、§23.2：敏感字段不得进入响应模型）。
    ``extra="forbid"`` 防止意外泄露未声明的字段。

    Attributes:
        id: 用户 UUID
        username: 用户名
        display_name: 显示名称
        status: 用户状态（``active`` 或 ``disabled``）
        phone: 手机号（可能为 null）
        email: 邮箱（可能为 null）
        last_login_at: 最近登录时间（可能为 null）
        password_updated_at: 密码更新时间
        created_at: 创建时间
        created_by: 创建人 ID（可能为 null）
        updated_at: 更新时间
        updated_by: 更新人 ID（可能为 null）
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    username: str
    display_name: str
    status: str
    phone: str | None
    email: str | None
    last_login_at: datetime | None
    password_updated_at: datetime
    created_at: datetime
    created_by: UUID | None
    updated_at: datetime
    updated_by: UUID | None
