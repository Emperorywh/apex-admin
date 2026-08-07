"""示例模块 SQLAlchemy ORM 模型（SPEC §5.4）。

ORM 模型与领域实体分离——Repository Adapter 负责在两者之间转换
（SPEC §5.2：禁止把 ORM 模型直接作为所有 API 响应模型）。

表结构通过 Alembic 迁移文件创建（手写 DDL，不使用 autogenerate，
SPEC §8.2）。ORM 模型用于查询和持久化，不暴露给 API 层。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.modules.example.domain.model import ExampleItem


class Base(DeclarativeBase):
    """示例模块 ORM 声明基类。

    G1 阶段各模块维护自身的 ``DeclarativeBase``；后续可根据需要
    提取为共享基类。迁移文件手写 DDL，此基类仅用于运行时 ORM 操作。
    """


class ExampleItemModel(Base):
    """示例项目 ORM 模型。

    表名 ``example_items``，通过 Alembic 迁移 ``0002_example`` 创建。

    Attributes:
        id: 主键 UUID
        name: 名称，最长 100 字符
        created_at: 创建时间（UTC，带时区）
    """

    __tablename__ = "example_items"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    @staticmethod
    def from_entity(entity: ExampleItem) -> ExampleItemModel:
        """从领域实体构造 ORM 模型。"""
        return ExampleItemModel(
            id=entity.id,
            name=entity.name,
            created_at=entity.created_at,
        )

    def to_entity(self) -> ExampleItem:
        """转换为领域实体。"""
        # 确保 created_at 带时区（数据库可能返回 naive datetime）
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return ExampleItem(
            id=self.id,
            name=self.name,
            created_at=created_at,
        )
