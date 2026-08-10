"""认证模块领域实体 — SPEC 12.2 / 12.3 / 12.4.

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2: "领域规则不得依赖 FastAPI、ORM、HTTP 或具体存储 SDK"）。

DTO、领域对象和 ORM 模型职责分离（SPEC 5.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


@dataclass(frozen=True)
class Session:
    """服务端会话领域实体 — SPEC 12.3.

    SPEC 12.3: "会话信息持久化到 PostgreSQL"。
    SPEC 12.3: "会话记录设备、IP、User-Agent、创建时间和最近活动时间"。

    数据库只保存 Access Token 的 HMAC-SHA-256 摘要（SPEC 12.2），
    明文 Token 仅在登录响应体中返回一次（SPEC 12.1）。

    属性:
        id:                   会话全局唯一标识（UUID）。
        user_id:              所属用户 ID（跨模块引用，不做数据库外键）。
        access_token_digest:  Access Token 的 HMAC-SHA-256 摘要（64 字符十六进制）。
        device:               设备标识（可为空）。
        ip_address:           创建时的客户端 IP 地址。
        user_agent:           创建时的 User-Agent（可为空）。
        created_at:           会话创建时间（UTC）。
        last_activity_at:     最近活动时间（UTC，最多每 5 分钟更新一次）。
        absolute_expires_at:  绝对过期时间（created_at + 12 小时，SPEC 12.3）。
        token_expires_at:     Access Token 过期时间（created_at + 15 分钟，SPEC 12.1）。
        revoked:              是否已吊销。
        revoked_reason:       吊销原因（可为空）。
    """

    id: UUID
    user_id: UUID
    access_token_digest: str
    device: str | None
    ip_address: str
    user_agent: str | None
    created_at: datetime
    last_activity_at: datetime
    absolute_expires_at: datetime
    token_expires_at: datetime
    revoked: bool
    revoked_reason: str | None


@dataclass(frozen=True)
class LoginAttempt:
    """登录失败计数字段实体 — SPEC 12.4.

    SPEC 12.4: "登录失败状态持久化到 PostgreSQL，以规范化账号标识和
    可信客户端 IP 作为独立维度统计"。

    两个独立维度:
      - ``dimension="account"``: 以规范化用户名（小写）为 key。
        连续失败 5 次后限制 15 分钟；成功登录后清理。
      - ``dimension="ip"``: 以可信客户端 IP 为 key。
        连续失败 20 次后限制 15 分钟；成功登录不清理，到期自动解除。

    属性:
        id:             记录全局唯一标识（UUID）。
        dimension:      维度标识（``"account"`` 或 ``"ip"``）。
        key:            维度键值（规范化用户名或客户端 IP）。
        failed_count:   连续失败次数。
        last_failed_at: 最近失败时间（UTC，可为空）。
        locked_until:   锁定截止时间（UTC，可为空；超过此时间自动解除）。
    """

    id: UUID
    dimension: str
    key: str
    failed_count: int
    last_failed_at: datetime | None
    locked_until: datetime | None


@dataclass(frozen=True)
class RefreshToken:
    """Refresh Token 领域实体 — SPEC 12.2.

    SPEC 12.2: "每个 Refresh Token 记录所属 Session、Token Family、前驱、
    创建时间、使用时间、过期时间和吊销原因"。

    SPEC 12.2: "数据库只保存 ... HMAC-SHA-256 摘要，不保存明文 Token"。

    刷新时旧 Token 标记 ``used_at``，新 Token 插入同一 ``family_id`` 链。
    已使用 Token 再次出现时视为重放，立即吊销整个 Session 和 Token Family。

    属性:
        id:              Token 全局唯一标识（UUID）。
        session_id:      所属会话 ID（跨模块引用 auth_sessions）。
        family_id:       Token Family 标识 — 同一登录会话的全部轮换 Token
                         共享同一 ``family_id``，重放检测按 Family 吊销。
        token_digest:    HMAC-SHA-256 摘要（64 字符十六进制），不存明文。
        predecessor_id:  前驱 Token ID（首个 Token 为 None）。
        created_at:      创建时间（UTC）。
        used_at:         使用时间（UTC，未使用为 None）。
        expires_at:      过期时间（UTC，不晚于会话绝对过期）。
        revoked_reason:  吊销原因（未吊销为 None）。
    """

    id: UUID
    session_id: UUID
    family_id: UUID
    token_digest: str
    predecessor_id: UUID | None
    created_at: datetime
    used_at: datetime | None
    expires_at: datetime
    revoked_reason: str | None
