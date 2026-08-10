"""认证模块请求与响应 Schema — SPEC 9.2 / 9.3 / 12.1.

SPEC 9.2 约定:
  - 请求模型使用 ``extra="forbid"``。
  - 字符串长度、格式具有约束。

SPEC 12.1: Access Token 仅在登录响应体中返回一次。
SPEC 12.4: 登录和刷新响应必须设置 ``Cache-Control: no-store``
（Cache-Control 头由 Router 设置，不在 Schema 中）。

SPEC 9.3: JSON 字段统一使用 snake_case，时间为带时区 ISO 8601。
SPEC 23.2: "禁止记录和回显密码"。响应中不包含密码字段。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, Field

from app.core.api.schemas import StrictBaseModel


class LoginRequest(StrictBaseModel):
    """登录请求 — SPEC 12.1 / 9.2.

    SPEC 12.1: "用户名和密码登录"。
    继承 ``StrictBaseModel``（``extra="forbid"``），携带未知字段返回 422。

    属性:
        username: 用户名/登录账号。
        password: 明文密码（不在日志中记录，SPEC 23.2 / 12.4）。
        device:   设备标识（可选），用于会话记录设备信息。
    """

    username: str = Field(
        min_length=1,
        max_length=100,
        description="用户名/登录账号",
    )
    password: str = Field(
        min_length=1,
        max_length=128,
        description="明文密码",
    )
    device: str | None = Field(
        default=None,
        max_length=200,
        description="设备标识（可选）",
    )


class LoginResponse(BaseModel):
    """登录成功响应 — SPEC 12.1.

    SPEC 12.1: "Access Token 仅在登录或刷新响应体中返回一次"。
    SPEC 12.1: "G2 固定使用不透明随机 Bearer Access Token，不使用 JWT"。
    ``access_token`` 为不透明随机字符串，仅在本次响应中返回，
    前端只允许保存在内存中。

    SPEC 12.4: 登录响应必须设置 ``Cache-Control: no-store``
    （由 Router 设置响应头，不在响应体中）。

    属性:
        access_token: 不透明 Access Token 字符串（仅在响应体中返回一次）。
        token_type:   固定为 ``"Bearer"``。
        expires_in:   Token 有效期（秒），默认 900（15 分钟）。
    """

    model_config = {"extra": "forbid"}

    access_token: str = Field(description="不透明 Access Token（仅返回一次）")
    token_type: str = Field(default="Bearer", description="Token 类型")
    expires_in: int = Field(description="Token 有效期（秒）")


class SessionResponse(BaseModel):
    """活动会话响应 — SPEC 12.3.

    SPEC 12.3: "用户可以查看自己的活动会话"。
    响应不含 Access Token 摘要（敏感字段不回显，SPEC 9.3）。

    属性:
        id:                会话 ID。
        device:            设备标识。
        ip_address:        创建时客户端 IP。
        user_agent:        创建时 User-Agent。
        created_at:        创建时间（UTC ISO 8601）。
        last_activity_at:  最近活动时间（UTC ISO 8601）。
        token_expires_at:  Token 过期时间（UTC ISO 8601）。
    """

    model_config = {"extra": "forbid"}

    id: UUID
    device: str | None
    ip_address: str
    user_agent: str | None
    created_at: datetime
    last_activity_at: datetime
    token_expires_at: datetime


class LogoutResponse(BaseModel):
    """退出登录响应."""

    model_config = {"extra": "forbid"}

    revoked_count: int = Field(description="被吊销的会话数量")
