"""RBAC ORM 模型 — SPEC 8.3 / 13.1.

SPEC 8.3 数据建模规范:
  - 每张业务表具有明确主键。
  - 表名、字段名、索引名遵循统一规范。
  - 唯一性规则优先由数据库唯一约束保证。
  - 时间字段使用 ``timestamptz``，统一 UTC（SPEC 6.3）。

SPEC 13.1 RBAC 模型:
  - 角色、权限点、用户角色关系、角色权限点关系。

SPEC 5.5: 跨模块数据库外键默认禁止。``rbac_user_roles.user_id`` 不做外键，
引用 identity 模块的 ``users`` 表，通过应用层 Port 校验存在性。

ORM 模型继承自全局 ``Base``，Alembic 通过 ``Base.metadata`` 收集表结构
（SPEC 8.2）。ORM 模型只在 Infrastructure 层使用，不泄漏到 Application
或 API 层（SPEC 5.2）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class RoleORM(Base):
    """角色 ORM 模型 — 映射 ``rbac_roles`` 表（SPEC 13.1 / 13.2）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``code`` 具有唯一约束，保证角色编码全局唯一。
      - 时间字段使用 ``DateTime(timezone=True)``（PostgreSQL ``timestamptz``）。

    SPEC 13.2: "系统内置角色具有明确保护规则"。
    ``is_builtin`` 标记系统内置角色，内置角色不可删除或禁用。
    """

    __tablename__ = "rbac_roles"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
        Index("ix_rbac_roles_code_unique", code, unique=True),
        Index("ix_rbac_roles_status", status),
    )


class PermissionORM(Base):
    """权限点 ORM 模型 — 映射 ``rbac_permissions`` 表（SPEC 13.1 / 25.2）.

    SPEC 13.1: 权限点使用稳定编码，表达资源和操作。
    SPEC 25.2: 权限点通过 ``sync-permissions`` 命令从各模块声明同步。
    """

    __tablename__ = "rbac_permissions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    module_code: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_rbac_permissions_code_unique", code, unique=True),
        Index("ix_rbac_permissions_module", module_code),
    )


class RolePermissionORM(Base):
    """角色-权限点关联 ORM 模型 — 映射 ``rbac_role_permissions`` 表（SPEC 13.1）.

    SPEC 13.1: "角色与权限点关系"。
    多对多关联表，复合主键 (role_id, permission_id)。
    """

    __tablename__ = "rbac_role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("rbac_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        ForeignKey("rbac_permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (Index("ix_rbac_role_permissions_permission", permission_id),)


class UserRoleORM(Base):
    """用户-角色关联 ORM 模型 — 映射 ``rbac_user_roles`` 表（SPEC 13.1 / 13.2）.

    SPEC 13.1: "用户与角色关系"。
    SPEC 13.2: "为用户分配角色"、"移除用户角色"、"查询角色成员"。

    SPEC 5.5: 跨模块数据库外键默认禁止。``user_id`` 不做外键约束。
    """

    __tablename__ = "rbac_user_roles"

    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("rbac_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (Index("ix_rbac_user_roles_role", role_id),)
