"""菜单模块 ORM 模型 — SPEC 8.3 / 15.1 / 15.2.

SPEC 8.3 数据建模规范:
  - 每张业务表具有明确主键。
  - 表名、字段名、索引名遵循统一规范。
  - 唯一性规则优先由数据库唯一约束保证。
  - 时间字段使用 ``timestamptz``，统一 UTC（SPEC 6.3）。

SPEC 15.1 菜单资源:
  - 菜单为树形实体，通过 ``parent_id`` 自引用实现父子层级。
  - 支持目录/页面/外链类型与前端路由元数据。

SPEC 15.2 角色菜单:
  - ``menu_role_menus`` 关联表存储角色-菜单关系。
  - ``role_id`` 引用 RBAC 模块角色 ID（跨模块不建数据库外键，SPEC 5.5）。

SPEC 5.5: ``role_id`` 不做外键约束，引用 RBAC 模块的 ``rbac_roles`` 表，
通过应用层 Port 校验角色存在性。``parent_id`` 为同模块自引用外键。

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


class MenuORM(Base):
    """菜单 ORM 模型 — 映射 ``menu_menus`` 表（SPEC 15.1）.

    SPEC 8.3:
      - 主键 ``id`` 为 UUID。
      - ``parent_id`` 自引用外键（同模块），实现树形层级。
      - 时间字段使用 ``DateTime(timezone=True)``（PostgreSQL ``timestamptz``）。

    SPEC 15.1:
      - ``menu_type`` 区分目录/页面/外链。
      - ``name``/``path``/``component``/``icon`` 为前端路由元数据。
      - ``visible`` 控制可见性（仅前端展示，SPEC 23.5）。

    SPEC 5.5: ``parent_id`` 使用 ``ondelete=RESTRICT`` 防止删除有子菜单的菜单时
    数据库层面的孤儿记录（应用层已在 Use Case 中拒绝，此处为终极防护）。
    """

    __tablename__ = "menu_menus"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("menu_menus.id", ondelete="RESTRICT"),
        nullable=True,
    )
    menu_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    component: Mapped[str | None] = mapped_column(String(500), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
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
        Index("ix_menu_menus_parent", parent_id),
        Index("ix_menu_menus_status", status),
    )


class RoleMenuORM(Base):
    """角色-菜单关联 ORM 模型 — 映射 ``menu_role_menus`` 表（SPEC 15.1 / 15.2）.

    SPEC 15.1: "为角色分配和移除菜单"。
    SPEC 15.2: "根据当前用户角色返回可访问菜单树"。

    多对多关联表，复合主键 (role_id, menu_id)。

    SPEC 5.5: ``role_id`` 跨模块引用 RBAC 模块 ``rbac_roles`` 表，
    不做数据库外键约束，通过应用层 Port 校验角色存在性。
    ``menu_id`` 为同模块自引用外键（menu_menus）。
    """

    __tablename__ = "menu_role_menus"

    role_id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True)
    menu_id: Mapped[UUID] = mapped_column(
        ForeignKey("menu_menus.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (Index("ix_menu_role_menus_menu", menu_id),)
