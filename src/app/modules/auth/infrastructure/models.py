"""认证模块 SQLAlchemy ORM 模型（SPEC §5.4、§12.2、§12.3）。

ORM 模型与领域实体分离——Repository Adapter 负责在两者之间转换
（SPEC §5.2：禁止把 ORM 模型直接作为 API 响应模型）。

表结构通过 Alembic 迁移文件创建（手写 DDL，SPEC §8.2）。

安全约束（SPEC §12.2）：
- Token 表只保存 HMAC-SHA-256 摘要列，不保存明文 Token 列
- Access Token 和 Refresh Token 使用不同密钥计算摘要

会话表记录设备、IP、User-Agent、创建时间、最近活动时间
和超时配置（SPEC §12.3）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.modules.auth.domain.login_security import (
    LoginAttempt,
    LoginAttemptDimension,
)
from app.modules.auth.domain.model import (
    AccessTokenRecord,
    RefreshTokenRecord,
    Session,
    SessionStatus,
)
from app.modules.auth.domain.tokens import DIGEST_HEX_LENGTH


class Base(DeclarativeBase):
    """认证模块 ORM 声明基类。

    G2 阶段各模块维护自身的 ``DeclarativeBase``；迁移文件手写 DDL，
    此基类仅用于运行时 ORM 操作。
    """


class SessionModel(Base):
    """会话 ORM 模型（SPEC §12.3）。

    表名 ``sessions``，通过 Alembic 迁移 ``0004_auth`` 创建。

    记录设备、IP、User-Agent、创建时间、最近活动时间和超时配置。
    状态使用 ``String`` 存储稳定编码（SPEC §8.3）。

    Attributes:
        id: 主键 UUID
        user_id: 所属用户 UUID
        device: 设备标识（可空）
        ip: 客户端 IP
        user_agent: 客户端 User-Agent
        created_at: 创建时间
        last_activity_at: 最近活动时间
        idle_timeout_minutes: 空闲超时（分钟）
        absolute_timeout_hours: 绝对超时（小时）
        status: 会话状态（``active`` / ``revoked``）
        revoked_at: 吊销时间（可空）
        revoked_reason: 吊销原因（可空）
    """

    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    device: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    idle_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    absolute_timeout_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    @staticmethod
    def from_entity(entity: Session) -> SessionModel:
        """从领域实体构造 ORM 模型。"""
        return SessionModel(
            id=entity.id,
            user_id=entity.user_id,
            device=entity.device,
            ip=entity.ip,
            user_agent=entity.user_agent,
            created_at=entity.created_at,
            last_activity_at=entity.last_activity_at,
            idle_timeout_minutes=entity.idle_timeout_minutes,
            absolute_timeout_hours=entity.absolute_timeout_hours,
            status=entity.status.value,
            revoked_at=entity.revoked_at,
            revoked_reason=entity.revoked_reason,
        )

    def to_entity(self) -> Session:
        """转换为领域实体。"""

        def _ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        return Session(
            id=self.id,
            user_id=self.user_id,
            device=self.device,
            ip=self.ip,
            user_agent=self.user_agent,
            created_at=_ensure_tz(self.created_at),  # type: ignore[arg-type]
            last_activity_at=_ensure_tz(self.last_activity_at),  # type: ignore[arg-type]
            idle_timeout_minutes=self.idle_timeout_minutes,
            absolute_timeout_hours=self.absolute_timeout_hours,
            status=SessionStatus(self.status),
            revoked_at=_ensure_tz(self.revoked_at),
            revoked_reason=self.revoked_reason,
        )


class AccessTokenModel(Base):
    """Access Token 摘要 ORM 模型（SPEC §12.2）。

    表名 ``access_tokens``。只保存 HMAC-SHA-256 摘要，不保存明文 Token
    （SPEC §12.2：数据库只保存 Access Token 的 HMAC-SHA-256 摘要）。

    Attributes:
        digest: Access Token 的 HMAC-SHA-256 摘要（主键，hex 编码）
        session_id: 所属会话 UUID
        user_id: 所属用户 UUID
        created_at: 创建时间
        expires_at: 过期时间
    """

    __tablename__ = "access_tokens"

    digest: Mapped[str] = mapped_column(
        String(DIGEST_HEX_LENGTH),
        primary_key=True,
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("sessions.id"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    @staticmethod
    def from_entity(entity: AccessTokenRecord) -> AccessTokenModel:
        """从领域实体构造 ORM 模型。"""
        return AccessTokenModel(
            digest=entity.digest,
            session_id=entity.session_id,
            user_id=entity.user_id,
            created_at=entity.created_at,
            expires_at=entity.expires_at,
        )

    def to_entity(self) -> AccessTokenRecord:
        """转换为领域实体。"""

        def _ensure_tz(dt: datetime) -> datetime:
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        return AccessTokenRecord(
            digest=self.digest,
            session_id=self.session_id,
            user_id=self.user_id,
            created_at=_ensure_tz(self.created_at),
            expires_at=_ensure_tz(self.expires_at),
        )


class RefreshTokenModel(Base):
    """Refresh Token 摘要 ORM 模型（SPEC §12.2）。

    表名 ``refresh_tokens``。只保存使用独立密钥计算的 HMAC-SHA-256 摘要，
    不保存明文 Token（SPEC §12.2）。

    记录 Token Family、前驱和时间信息，支持 TASK-016 的轮换和重放检测。

    Attributes:
        digest: Refresh Token 的 HMAC-SHA-256 摘要（主键，独立密钥）
        session_id: 所属会话 UUID
        user_id: 所属用户 UUID
        token_family_id: Token Family UUID
        predecessor_digest: 前驱 Token 摘要（首个 Token 为 None）
        created_at: 创建时间
        used_at: 使用时间（可空）
        expires_at: 过期时间
        revoked_reason: 吊销原因（可空）
    """

    __tablename__ = "refresh_tokens"

    digest: Mapped[str] = mapped_column(
        String(DIGEST_HEX_LENGTH),
        primary_key=True,
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("sessions.id"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    token_family_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    predecessor_digest: Mapped[str | None] = mapped_column(
        String(DIGEST_HEX_LENGTH),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    @staticmethod
    def from_entity(entity: RefreshTokenRecord) -> RefreshTokenModel:
        """从领域实体构造 ORM 模型。"""
        return RefreshTokenModel(
            digest=entity.digest,
            session_id=entity.session_id,
            user_id=entity.user_id,
            token_family_id=entity.token_family_id,
            predecessor_digest=entity.predecessor_digest,
            created_at=entity.created_at,
            used_at=entity.used_at,
            expires_at=entity.expires_at,
            revoked_reason=entity.revoked_reason,
        )

    def to_entity(self) -> RefreshTokenRecord:
        """转换为领域实体。"""

        def _ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        return RefreshTokenRecord(
            digest=self.digest,
            session_id=self.session_id,
            user_id=self.user_id,
            token_family_id=self.token_family_id,
            predecessor_digest=self.predecessor_digest,
            created_at=_ensure_tz(self.created_at),  # type: ignore[arg-type]
            used_at=_ensure_tz(self.used_at),
            expires_at=_ensure_tz(self.expires_at),  # type: ignore[arg-type]
            revoked_reason=self.revoked_reason,
        )


class LoginAttemptModel(Base):
    """登录失败记录 ORM 模型（SPEC §12.4）。

    表名 ``login_attempts``，通过 Alembic 迁移 ``0005_login_security`` 创建。

    以维度（``account`` / ``ip``）和标识符为复合主键，统计连续失败次数
    和限制状态。暴力破解防护基于 PostgreSQL 持久化以跨多 Worker 工作
    （SPEC §12.4）。

    Attributes:
        dimension: 统计维度（``account`` 或 ``ip``）
        identifier: 标识符（规范化账号名或可信客户端 IP）
        failure_count: 连续失败次数
        locked_until: 限制截止时间（None 表示未限制）
        last_failure_at: 最近失败时间
    """

    __tablename__ = "login_attempts"

    dimension: Mapped[str] = mapped_column(String(10), primary_key=True)
    identifier: Mapped[str] = mapped_column(String(255), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_failure_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    @staticmethod
    def from_entity(entity: LoginAttempt) -> LoginAttemptModel:
        """从领域实体构造 ORM 模型。"""
        return LoginAttemptModel(
            dimension=entity.dimension.value,
            identifier=entity.identifier,
            failure_count=entity.failure_count,
            locked_until=entity.locked_until,
            last_failure_at=entity.last_failure_at,
        )

    def to_entity(self) -> LoginAttempt:
        """转换为领域实体。"""

        def _ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        return LoginAttempt(
            dimension=LoginAttemptDimension(self.dimension),
            identifier=self.identifier,
            failure_count=self.failure_count,
            locked_until=_ensure_tz(self.locked_until),
            last_failure_at=_ensure_tz(self.last_failure_at),  # type: ignore[arg-type]
        )
