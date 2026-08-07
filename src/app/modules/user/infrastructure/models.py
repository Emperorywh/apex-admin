"""用户模块 SQLAlchemy ORM 模型（SPEC §5.4、§11.2）。

ORM 模型与领域实体分离——Repository Adapter 负责在两者之间转换
（SPEC §5.2：禁止把 ORM 模型直接作为所有 API 响应模型）。

表结构通过 Alembic 迁移文件创建（手写 DDL，不使用 autogenerate，
SPEC §8.2）。ORM 模型用于查询和持久化，不暴露给 API 层。

用户状态使用 ``String`` 存储枚举稳定编码（SPEC §8.3），
而非数据库原生枚举类型，保持迁移灵活性。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.modules.user.domain.model import User, UserStatus


class Base(DeclarativeBase):
    """用户模块 ORM 声明基类。

    G2 阶段各模块维护自身的 ``DeclarativeBase``；迁移文件手写 DDL，
    此基类仅用于运行时 ORM 操作。
    """


class UserModel(Base):
    """用户 ORM 模型。

    表名 ``users``，通过 Alembic 迁移 ``0003_user`` 创建。

    ``username`` 具有唯一索引，保证用户名全局唯一（SPEC §11.2）。
    ``password_hash`` 存储完整的 Argon2id 编码哈希字符串，
    不在响应中暴露。

    Attributes:
        id: 主键 UUID
        username: 用户名，唯一
        display_name: 显示名称
        password_hash: Argon2id 密码哈希
        status: 用户状态（``active`` / ``disabled``，SPEC §8.3 稳定编码）
        phone: 手机号（可空）
        email: 邮箱（可空）
        last_login_at: 最近登录时间（可空）
        password_updated_at: 密码更新时间
        created_at: 创建时间
        created_by: 创建人 ID（可空，审计字段）
        updated_at: 更新时间
        updated_by: 更新人 ID（可空，审计字段）
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    password_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    @staticmethod
    def from_entity(entity: User) -> UserModel:
        """从领域实体构造 ORM 模型。"""
        return UserModel(
            id=entity.id,
            username=entity.username,
            display_name=entity.display_name,
            password_hash=entity.password_hash,
            status=entity.status.value,
            phone=entity.phone,
            email=entity.email,
            last_login_at=entity.last_login_at,
            password_updated_at=entity.password_updated_at,
            created_at=entity.created_at,
            created_by=entity.created_by,
            updated_at=entity.updated_at,
            updated_by=entity.updated_by,
        )

    def to_entity(self) -> User:
        """转换为领域实体。"""

        # 确保时间字段带时区（数据库可能返回 naive datetime）
        def _ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        return User(
            id=self.id,
            username=self.username,
            display_name=self.display_name,
            password_hash=self.password_hash,
            status=UserStatus(self.status),
            phone=self.phone,
            email=self.email,
            last_login_at=_ensure_tz(self.last_login_at),
            password_updated_at=_ensure_tz(self.password_updated_at),  # type: ignore[arg-type]
            created_at=_ensure_tz(self.created_at),  # type: ignore[arg-type]
            created_by=self.created_by,
            updated_at=_ensure_tz(self.updated_at),  # type: ignore[arg-type]
            updated_by=self.updated_by,
        )
