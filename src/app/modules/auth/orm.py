"""认证模块 ORM 模型 — SPEC 8.3 / 12.2 / 12.3 / 12.4.

SPEC 8.3 数据建模规范:
  - 每张业务表具有明确主键。
  - 时间字段使用 ``timestamptz``，统一 UTC（SPEC 6.3）。
  - 唯一性规则优先由数据库唯一约束保证。

SPEC 12.2: "数据库只保存 Access Token 和 Refresh Token 的 HMAC-SHA-256 摘要，
不保存明文 Token"。

SPEC 5.5: "跨模块数据库外键默认禁止"。``auth_sessions.user_id`` 引用 ``users.id``
但不创建数据库外键约束，由应用层保证引用完整性。

ORM 模型只在 Infrastructure 层使用，不泄漏到 Application 或 API 层
（SPEC 5.2）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class SessionORM(Base):
    """服务端会话 ORM 模型 — 映射 ``auth_sessions`` 表（SPEC 12.3）.

    SPEC 12.2: ``access_token_digest`` 存储 HMAC-SHA-256 摘要，不存明文 Token。
    SPEC 12.3: 会话记录设备、IP、User-Agent、创建时间、最近活动时间、过期时间。
    SPEC 5.5: ``user_id`` 不做数据库外键（跨模块外键默认禁止）。

    唯一约束: ``access_token_digest`` 全局唯一，保证每个 Access Token 摘要
    最多对应一条会话记录。
    """

    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    access_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    device: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(100), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    revoked_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    __table_args__ = (
        # Access Token 摘要唯一索引 — SPEC 12.2: 每个摘要最多一条会话。
        # 认证依赖通过摘要查找会话，唯一约束保证查找结果唯一。
        Index("ix_auth_sessions_token_digest_unique", access_token_digest, unique=True),
        # 用户 ID 索引 — 支持按用户查询活动会话和批量吊销。
        Index("ix_auth_sessions_user_id", user_id),
    )


class LoginAttemptORM(Base):
    """登录失败计数 ORM 模型 — 映射 ``auth_login_attempts`` 表（SPEC 12.4）.

    SPEC 12.4: "登录失败状态持久化到 PostgreSQL，以规范化账号标识和
    可信客户端 IP 作为独立维度统计"。

    两个独立维度通过 ``dimension`` 列区分:
      - ``"account"``: ``key`` 为规范化用户名（小写）。
      - ``"ip"``: ``key`` 为客户端 IP 地址。

    (dimension, key) 唯一约束保证每个维度键只有一条计数记录。
    """

    __tablename__ = "auth_login_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # 维度+键唯一约束 — 每个维度键只有一条计数记录。
        Index(
            "ix_auth_login_attempts_dimension_key_unique",
            dimension,
            key,
            unique=True,
        ),
    )
