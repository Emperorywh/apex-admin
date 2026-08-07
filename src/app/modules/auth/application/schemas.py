"""认证模块请求/响应 Schema（SPEC §9.2、§9.3、§12.1）。

继承 :class:`~app.api.schemas.BaseRequestModel` 和
:class:`~app.api.schemas.BaseResponseModel`，确保未知字段被拒绝
（``extra="forbid"``）和 snake_case 命名一致。

登录请求不强制密码长度校验——任何密码输入都应经过 Argon2id 验证，
以避免通过请求层校验泄露密码策略或区分用户是否存在（SPEC §12.4：
防止通过错误响应枚举有效用户）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.api.schemas import BaseRequestModel, BaseResponseModel

# ---------------------------------------------------------------------------
# 请求 Schema（SPEC §9.2：extra="forbid"）
# ---------------------------------------------------------------------------


class LoginRequest(BaseRequestModel):
    """登录请求 Schema（SPEC §12.1、§12.4）。

    不强制密码长度约束——任何密码输入都经过 Argon2id 验证，
    防止通过校验差异枚举用户（SPEC §12.4）。

    Attributes:
        username: 用户名
        password: 明文密码
    """

    username: str = Field(
        min_length=1,
        max_length=50,
        description="用户名",
    )
    password: str = Field(
        min_length=1,
        description="密码",
    )


# ---------------------------------------------------------------------------
# 响应 Schema（SPEC §9.3）
# ---------------------------------------------------------------------------


class LoginResponse(BaseResponseModel):
    """登录响应 Schema（SPEC §12.1、§12.2）。

    Access Token 在响应体中返回一次（SPEC §12.1：仅在登录或刷新
    响应体中返回一次，前端只允许保存在内存中）。

    Refresh Token 不出现在 JSON 响应中——只通过 HttpOnly Cookie 传递
    （SPEC §12.1）。响应必须设置 ``Cache-Control: no-store``
    （SPEC §12.1、§12.2）。

    Attributes:
        access_token: Access Token 明文（不透明，一次性）
        token_type: Token 类型，固定 ``Bearer``
        expires_in: Access Token 有效期（秒），默认 900（15 分钟）
        session_id: 新创建会话的 UUID
    """

    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(description="Access Token（不透明，仅返回一次）")
    token_type: str = Field(
        default="Bearer",
        description="Token 类型",
    )
    expires_in: int = Field(
        description="Access Token 有效期（秒）",
    )
    session_id: UUID = Field(description="新创建会话的 UUID")


class RefreshResponse(BaseResponseModel):
    """刷新响应 Schema（SPEC §12.2）。

    与登录响应结构一致：Access Token 在响应体中返回一次，
    新 Refresh Token 通过 HttpOnly Cookie 传递。
    响应必须设置 ``Cache-Control: no-store``。
    """

    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(description="新 Access Token（不透明，仅返回一次）")
    token_type: str = Field(
        default="Bearer",
        description="Token 类型",
    )
    expires_in: int = Field(
        description="Access Token 有效期（秒）",
    )
    session_id: UUID = Field(description="所属会话的 UUID")


class SessionItem(BaseResponseModel):
    """会话列表项 Schema（SPEC §12.3）。

    Attributes:
        id: 会话 UUID
        device: 设备标识
        ip: 客户端 IP
        user_agent: 客户端 User-Agent
        created_at: 创建时间
        last_activity_at: 最近活动时间
        status: 会话状态
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(description="会话 UUID")
    device: str | None = Field(description="设备标识")
    ip: str = Field(description="客户端 IP")
    user_agent: str = Field(description="客户端 User-Agent")
    created_at: datetime = Field(description="创建时间")
    last_activity_at: datetime = Field(description="最近活动时间")
    status: str = Field(description="会话状态")
