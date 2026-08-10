"""用户 ORM 模型 — SPEC 8.3 / 11.2.

SPEC 8.3 数据建模规范:
  - 每张业务表具有明确主键。
  - 表名、字段名、索引名遵循统一规范。
  - 唯一性规则优先由数据库唯一约束保证。
  - 时间字段使用 ``timestamptz``，统一 UTC（SPEC 6.3）。
  - 敏感字段明确哈希策略。

ORM 模型继承自全局 ``Base``，Alembic 通过 ``Base.metadata`` 收集表结构
（SPEC 8.2）。ORM 模型只在 Infrastructure 层使用，不泄漏到 Application
或 API 层（SPEC 5.2）。

``password_hash`` 列存储 Argon2id PHC 格式哈希字符串，不存储明文密码
（SPEC 12.1 / 23.2: "禁止记录和回显密码"）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class UserORM(Base):
    """用户 ORM 模型 — 映射 ``users`` 表（SPEC 11.2）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``username`` 具有唯一约束，保证用户名全局唯一
        （SPEC 8.3: "唯一性规则优先由数据库唯一约束保证"）。
      - 时间字段使用 ``DateTime(timezone=True)``（PostgreSQL ``timestamptz``）。
      - ``password_hash`` 存储 Argon2id 哈希，不存储明文。

    SPEC 11.3 删除策略:
      - 物理删除（DELETE）受审计记录保护——已产生审计记录的用户不得物理删除
        （SPEC 11.3: "已产生审计记录的用户不得因物理删除导致审计信息失真"）。
      - 此 ORM 模型不设置软删除列，禁用优先语义由 ``status`` 字段表达。

    用户状态以稳定字符串编码存储（``status`` 列），不使用数据库枚举类型
    （SPEC 8.3: "枚举值具有稳定编码"）。
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    password_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        # 用户名唯一约束 — SPEC 8.3: 唯一性规则优先由数据库唯一约束保证。
        # 冲突时由数据库拦截，翻译为稳定冲突错误码（SPEC 8.4）。
        Index("ix_users_username_unique", username, unique=True),
        # 状态索引 — 支持按状态筛选分页查询。
        Index("ix_users_status", status),
        # 创建时间索引 — 支持按创建时间排序分页。
        Index("ix_users_created_at", created_at),
    )
