"""RBAC 模块 SQLAlchemy ORM 模型（SPEC §5.4、§13.1）。

ORM 模型与领域实体分离——Repository Adapter 负责在两者之间转换
（SPEC §5.2）。

表结构通过 Alembic 迁移文件创建（手写 DDL，不使用 autogenerate，
SPEC §8.2）。

三张表：
- ``roles`` — 角色表
- ``user_roles`` — 用户-角色关系表（多对多）
- ``role_permissions`` — 角色-权限关系表（多对多，权限以编码引用）
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.modules.rbac.domain.model import Role, RoleStatus


class Base(DeclarativeBase):
    """RBAC 模块 ORM 声明基类。"""


class RoleModel(Base):
    """角色 ORM 模型。

    表名 ``roles``，通过 Alembic 迁移 ``0005_rbac`` 创建。

    ``code`` 具有唯一索引，保证角色编码全局唯一（SPEC §13.1）。
    ``is_super_admin`` 标志显式定义超级管理员角色（SPEC §13.4）。
    ``is_builtin`` 标记系统内置角色，受保护规则约束（SPEC §13.2）。
    """

    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_super_admin: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    @staticmethod
    def from_entity(entity: Role) -> RoleModel:
        """从领域实体构造 ORM 模型。"""
        return RoleModel(
            id=entity.id,
            code=entity.code,
            name=entity.name,
            status=entity.status.value,
            description=entity.description,
            is_builtin=entity.is_builtin,
            is_super_admin=entity.is_super_admin,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            created_by=entity.created_by,
            updated_by=entity.updated_by,
        )

    def to_entity(self) -> Role:
        """转换为领域实体。"""

        def _ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        return Role(
            id=self.id,
            code=self.code,
            name=self.name,
            status=RoleStatus(self.status),
            description=self.description,
            is_builtin=self.is_builtin,
            is_super_admin=self.is_super_admin,
            created_at=_ensure_tz(self.created_at),  # type: ignore[arg-type]
            updated_at=_ensure_tz(self.updated_at),  # type: ignore[arg-type]
            created_by=self.created_by,
            updated_by=self.updated_by,
        )


class UserRoleModel(Base):
    """用户-角色关系 ORM 模型（SPEC §13.1）。

    表名 ``user_roles``，复合主键 (user_id, role_id)。
    """

    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        primary_key=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("roles.id"),
        primary_key=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    assigned_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)


class RolePermissionModel(Base):
    """角色-权限关系 ORM 模型（SPEC §13.1）。

    表名 ``role_permissions``，复合主键 (role_id, permission_code)。
    权限以稳定编码引用，不外键到单独的权限表——权限点通过
    ModuleDefinition 声明和注册（SPEC §5.5、§13.1）。
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("roles.id"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
    )


class PermissionPointModel(Base):
    """权限点注册表 ORM 模型（SPEC §13.1、§25.2）。

    表名 ``permission_points``，存储所有启用模块声明的权限点。
    通过 ``sync-permissions`` 命令幂等同步（SPEC §25.2）。
    ``code`` 为主键，全局唯一且稳定（SPEC §5.5）。
    """

    __tablename__ = "permission_points"

    code: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    module_code: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
