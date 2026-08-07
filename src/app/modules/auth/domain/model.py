"""认证会话领域实体与常量（SPEC §12.3）。

``Session`` 是不可变领域实体，记录服务端会话的设备、IP、User-Agent、
创建时间、最近活动时间和超时配置。

会话超时常量遵循 SPEC §12.3：空闲超时 30 分钟、绝对超时 12 小时。
``SessionStatus`` 使用 ``StrEnum`` 确保稳定编码一致性（SPEC §8.3）。

此实体由认证模块 Use Case 管理，是 TASK-016（Token 轮换）和
TASK-017（登录安全）的基础。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

# ---------------------------------------------------------------------------
# 会话超时常量（SPEC §12.3）
# ---------------------------------------------------------------------------

#: 空闲超时时间——默认 30 分钟（SPEC §12.3）
IDLE_TIMEOUT_MINUTES: int = 30

#: 绝对超时时间——默认 12 小时（SPEC §12.3）
ABSOLUTE_TIMEOUT_HOURS: int = 12

#: Access Token 有效期——默认 15 分钟（SPEC §12.1）
ACCESS_TOKEN_TTL_MINUTES: int = 15

#: 最近活动时间条件更新间隔——最多每 5 分钟更新一次（SPEC §12.3）
ACTIVITY_UPDATE_INTERVAL_MINUTES: int = 5

#: 吊销原因常量（SPEC §12.2、§12.3）
REASON_LOGOUT: str = "logout"
REASON_ADMIN_FORCE_LOGOUT: str = "admin_force_logout"
REASON_USER_DISABLED: str = "user_disabled"
REASON_PASSWORD_RESET: str = "password_reset"
REASON_PASSWORD_CHANGED: str = "password_changed"
REASON_REPLAY_DETECTED: str = "replay_detected"
REASON_SESSION_EXPIRED: str = "session_expired"


class SessionStatus(enum.StrEnum):
    """会话状态枚举（SPEC §12.3、§8.3）。

    Attributes:
        ACTIVE: 活跃——会话有效，可继续使用
        REVOKED: 已吊销——会话已显式终止（登出、管理员强制下线等）
    """

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class Session:
    """服务端会话领域实体（SPEC §12.3）。

    会话信息持久化到 PostgreSQL，记录设备、IP、User-Agent、创建时间和
    最近活动时间。会话的有效性同时受空闲超时和绝对超时约束（SPEC §12.3）。

    实体不可变（frozen dataclass），修改操作通过 ``with_*`` 方法返回新实例。

    Attributes:
        id: 会话唯一标识（UUID）
        user_id: 所属用户 UUID
        device: 设备标识（可空，由客户端提供或由 User-Agent 推断）
        ip: 客户端 IP 地址（可信来源）
        user_agent: 客户端 User-Agent
        created_at: 创建时间（UTC）
        last_activity_at: 最近活动时间（UTC）
        idle_timeout_minutes: 空闲超时（分钟），默认 30
        absolute_timeout_hours: 绝对超时（小时），默认 12
        status: 会话状态
        revoked_at: 吊销时间（UTC，可空）
        revoked_reason: 吊销原因（可空）
    """

    id: UUID
    user_id: UUID
    device: str | None
    ip: str
    user_agent: str
    created_at: datetime
    last_activity_at: datetime
    idle_timeout_minutes: int
    absolute_timeout_hours: int
    status: SessionStatus
    revoked_at: datetime | None
    revoked_reason: str | None

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        *,
        user_id: UUID,
        ip: str,
        user_agent: str,
        device: str | None = None,
        current_time: datetime,
        idle_timeout_minutes: int = IDLE_TIMEOUT_MINUTES,
        absolute_timeout_hours: int = ABSOLUTE_TIMEOUT_HOURS,
    ) -> Session:
        """创建新会话实体。

        新会话初始状态为 ``ACTIVE``，创建时间和最近活动时间均设为
        ``current_time``。

        Args:
            user_id: 所属用户 UUID
            ip: 客户端 IP
            user_agent: 客户端 User-Agent
            device: 设备标识（可选）
            current_time: 当前 UTC 时间
            idle_timeout_minutes: 空闲超时（分钟）
            absolute_timeout_hours: 绝对超时（小时）

        Returns:
            新创建的 :class:`Session` 实例
        """
        return cls(
            id=uuid4(),
            user_id=user_id,
            device=device,
            ip=ip,
            user_agent=user_agent,
            created_at=current_time,
            last_activity_at=current_time,
            idle_timeout_minutes=idle_timeout_minutes,
            absolute_timeout_hours=absolute_timeout_hours,
            status=SessionStatus.ACTIVE,
            revoked_at=None,
            revoked_reason=None,
        )

    # ------------------------------------------------------------------
    # 状态变更方法（返回新实例）
    # ------------------------------------------------------------------

    def revoke(
        self,
        *,
        reason: str,
        current_time: datetime,
    ) -> Session:
        """返回已吊销的新实例（SPEC §12.3）。

        Args:
            reason: 吊销原因（如 ``logout``、``admin_force_logout``）
            current_time: 吊销时间（UTC）

        Returns:
            吊销后的 :class:`Session` 新实例
        """
        return replace(
            self,
            status=SessionStatus.REVOKED,
            revoked_at=current_time,
            revoked_reason=reason,
        )

    def touch(self, *, current_time: datetime) -> Session:
        """返回更新最近活动时间后的新实例（SPEC §12.3）。

        最近活动时间最多每 5 分钟条件更新一次（SPEC §12.3），
        调用方需自行判断是否需要调用此方法。
        """
        return replace(self, last_activity_at=current_time)

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """会话是否处于活跃状态。"""
        return self.status is SessionStatus.ACTIVE

    @property
    def is_revoked(self) -> bool:
        """会话是否已吊销。"""
        return self.status is SessionStatus.REVOKED

    @property
    def absolute_expiry(self) -> datetime:
        """会话绝对过期时间——创建时间 + 绝对超时。"""
        return self.created_at + timedelta(hours=self.absolute_timeout_hours)

    def is_expired(
        self,
        *,
        current_time: datetime | None = None,
    ) -> bool:
        """会话是否已过期（SPEC §12.3）。

        会话在以下任一条件满足时视为过期：
        - 状态为 ``REVOKED``
        - 超过绝对超时（创建时间 + 绝对超时 ≤ 当前时间）
        - 超过空闲超时（最近活动时间 + 空闲超时 ≤ 当前时间）

        Args:
            current_time: 当前 UTC 时间，默认为调用时刻

        Returns:
            过期返回 ``True``
        """
        now = current_time or datetime.now(UTC)
        if self.is_revoked:
            return True
        if now >= self.absolute_expiry:
            return True
        idle_deadline = self.last_activity_at + timedelta(
            minutes=self.idle_timeout_minutes,
        )
        return now >= idle_deadline


@dataclass(frozen=True)
class AccessTokenRecord:
    """Access Token 持久化记录（SPEC §12.1、§12.2）。

    存储 Access Token 的 HMAC-SHA-256 摘要（非明文），用于在线校验。
    每个会话同一时间最多一个有效 Access Token（SPEC §12.2）。

    Attributes:
        digest: Access Token 的 HMAC-SHA-256 摘要（hex）
        session_id: 所属会话 UUID
        user_id: 所属用户 UUID
        created_at: 创建时间（UTC）
        expires_at: 过期时间（UTC）
    """

    digest: str
    session_id: UUID
    user_id: UUID
    created_at: datetime
    expires_at: datetime

    @classmethod
    def new(
        cls,
        *,
        digest: str,
        session_id: UUID,
        user_id: UUID,
        created_at: datetime,
    ) -> AccessTokenRecord:
        """创建 Access Token 记录。

        过期时间为创建时间 + ACCESS_TOKEN_TTL_MINUTES（SPEC §12.1：15 分钟）。
        """
        return cls(
            digest=digest,
            session_id=session_id,
            user_id=user_id,
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
        )

    def is_expired(self, *, current_time: datetime | None = None) -> bool:
        """Token 是否已过期。"""
        now = current_time or datetime.now(UTC)
        return now >= self.expires_at


@dataclass(frozen=True)
class RefreshTokenRecord:
    """Refresh Token 持久化记录（SPEC §12.2、§12.3）。

    存储 Refresh Token 的 HMAC-SHA-256 摘要（使用独立密钥，非明文）。
    记录所属 Session、Token Family、前驱和时间信息，支持 TASK-016 的
    Token 轮换和重放检测。

    Refresh Token 的过期时间不得晚于会话绝对过期时间（SPEC §12.3）。

    Attributes:
        digest: Refresh Token 的 HMAC-SHA-256 摘要（hex，独立密钥）
        session_id: 所属会话 UUID
        user_id: 所属用户 UUID
        token_family_id: Token Family UUID（登录时新建，轮换时不变）
        predecessor_digest: 前驱 Token 摘要（首个 Token 为 None）
        created_at: 创建时间（UTC）
        used_at: 使用时间（UTC，登录时为 None）
        expires_at: 过期时间（UTC，不超过会话绝对过期时间）
        revoked_reason: 吊销原因（可空）
    """

    digest: str
    session_id: UUID
    user_id: UUID
    token_family_id: UUID
    predecessor_digest: str | None
    created_at: datetime
    used_at: datetime | None
    expires_at: datetime
    revoked_reason: str | None

    @classmethod
    def new(
        cls,
        *,
        digest: str,
        session_id: UUID,
        user_id: UUID,
        token_family_id: UUID,
        created_at: datetime,
        expires_at: datetime,
    ) -> RefreshTokenRecord:
        """创建 Refresh Token 记录（登录时调用）。

        首个 Token 无前驱（``predecessor_digest=None``）、未使用
        （``used_at=None``）、未吊销（``revoked_reason=None``）。
        """
        return cls(
            digest=digest,
            session_id=session_id,
            user_id=user_id,
            token_family_id=token_family_id,
            predecessor_digest=None,
            created_at=created_at,
            used_at=None,
            expires_at=expires_at,
            revoked_reason=None,
        )

    def is_expired(self, *, current_time: datetime | None = None) -> bool:
        """Token 是否已过期。"""
        now = current_time or datetime.now(UTC)
        return now >= self.expires_at

    @property
    def is_revoked(self) -> bool:
        """Token 是否已吊销。"""
        return self.revoked_reason is not None

    @property
    def is_used(self) -> bool:
        """Token 是否已被使用（轮换后）。"""
        return self.used_at is not None

    @property
    def is_usable(self) -> bool:
        """Token 是否可用于刷新——未使用、未吊销。"""
        return not self.is_used and not self.is_revoked

    def mark_used(self, *, current_time: datetime) -> RefreshTokenRecord:
        """返回标记为已使用的新实例（轮换时调用，SPEC §12.2）。"""
        return replace(self, used_at=current_time)

    def revoke(self, *, reason: str) -> RefreshTokenRecord:
        """返回标记为已吊销的新实例（SPEC §12.2）。"""
        return replace(self, revoked_reason=reason)

    def rotated(
        self,
        *,
        new_digest: str,
        current_time: datetime,
        expires_at: datetime,
    ) -> RefreshTokenRecord:
        """创建轮换后的新 Refresh Token 记录（SPEC §12.2）。

        新 Token 属于同一 Token Family，前驱为当前 Token 的摘要。
        """
        return RefreshTokenRecord(
            digest=new_digest,
            session_id=self.session_id,
            user_id=self.user_id,
            token_family_id=self.token_family_id,
            predecessor_digest=self.digest,
            created_at=current_time,
            used_at=None,
            expires_at=min(expires_at, self.expires_at),
            revoked_reason=None,
        )
