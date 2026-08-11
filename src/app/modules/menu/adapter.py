"""菜单模块 Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``MenuRepository``。
Adapter 在内部将 ORM 模型与领域实体互转，确保内层不感知 ORM 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, insert, select

from app.modules.menu.models import Menu, MenuStatus, MenuType
from app.modules.menu.orm import MenuORM, RoleMenuORM
from app.modules.menu.port import MenuRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

#: PostgreSQL 事务级咨询锁的键 — 序列化并发层级调整（SPEC 15.1）。
#: 固定整数键，确保所有层级调整事务竞争同一把锁。
#: 与 org 模块的 key 不同，避免锁竞争干扰。
_ADVISORY_LOCK_KEY = 40_015_001


class SqlAlchemyMenuRepository(MenuRepository):
    """SQLAlchemy 异步菜单 Repository Adapter — 实现 ``MenuRepository`` Port."""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    # ── 菜单 CRUD ───────────────────────────────────────────────────────

    async def add_menu(self, menu: Menu) -> None:
        """添加新菜单到当前事务."""

        orm = _menu_to_orm(menu)
        self._session.add(orm)
        await self._session.flush()

    async def get_menu_by_id(self, menu_id: UUID) -> Menu | None:
        """按 ID 查询菜单."""

        stmt = select(MenuORM).where(MenuORM.id == menu_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_menu(orm) if orm else None

    async def save_menu(self, menu: Menu) -> None:
        """保存菜单变更."""

        stmt = select(MenuORM).where(MenuORM.id == menu.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.menu.errors import MenuNotFoundError

            raise MenuNotFoundError(str(menu.id))

        orm.parent_id = menu.parent_id
        orm.menu_type = menu.menu_type.value
        orm.title = menu.title
        orm.name = menu.name
        orm.path = menu.path
        orm.component = menu.component
        orm.icon = menu.icon
        orm.sort_order = menu.sort_order
        orm.visible = menu.visible
        orm.status = menu.status.value
        orm.updated_at = menu.updated_at
        orm.updated_by = menu.updated_by
        await self._session.flush()

    async def delete_menu_by_id(self, menu_id: UUID) -> bool:
        """按 ID 物理删除菜单."""

        stmt = select(MenuORM).where(MenuORM.id == menu_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    async def list_all_menus(
        self,
        *,
        include_disabled: bool = True,
    ) -> list[Menu]:
        """查询全部菜单."""

        stmt = select(MenuORM).order_by(
            MenuORM.sort_order,
            MenuORM.title,
        )
        if not include_disabled:
            stmt = stmt.where(MenuORM.status == MenuStatus.ACTIVE.value)
        result = await self._session.execute(stmt)
        return [_orm_to_menu(orm) for orm in result.scalars().all()]

    # ── 循环防护 ────────────────────────────────────────────────────────

    async def get_descendant_ids(self, menu_id: UUID) -> set[UUID]:
        """查询菜单的全部后代 ID（递归）.

        使用 PostgreSQL 递归 CTE 遍历子菜单树。
        """

        from sqlalchemy import text

        recursive_sql = text(
            """
            WITH RECURSIVE descendants AS (
                SELECT id FROM menu_menus WHERE parent_id = :root_id
                UNION ALL
                SELECT m.id FROM menu_menus m
                INNER JOIN descendants dc ON m.parent_id = dc.id
            )
            SELECT id FROM descendants
            """,
        )
        result = await self._session.execute(recursive_sql, {"root_id": menu_id})
        return {row[0] for row in result.fetchall()}

    async def acquire_hierarchy_lock(self) -> None:
        """获取事务级咨询锁 — 序列化并发层级调整."""

        from sqlalchemy import text

        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        )

    # ── 删除保护 ────────────────────────────────────────────────────────

    async def count_children(self, menu_id: UUID) -> int:
        """查询菜单的直接子菜单数量."""

        stmt = (
            select(func.count())
            .select_from(MenuORM)
            .where(MenuORM.parent_id == menu_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    # ── 角色菜单关系 ────────────────────────────────────────────────────

    async def replace_role_menus(
        self,
        role_id: UUID,
        menu_ids: set[UUID],
        *,
        now: object,
    ) -> None:
        """替换角色的全部菜单 — 全量覆盖."""

        from datetime import datetime

        assert isinstance(now, datetime)

        # 删除现有全部关联
        await self._session.execute(
            delete(RoleMenuORM).where(RoleMenuORM.role_id == role_id),
        )

        # 插入新关联
        for mid in menu_ids:
            await self._session.execute(
                insert(RoleMenuORM).values(
                    role_id=role_id,
                    menu_id=mid,
                    created_at=now,
                ),
            )
        await self._session.flush()

    async def remove_role_menu(self, role_id: UUID, menu_id: UUID) -> bool:
        """移除角色单个菜单关联."""

        check_stmt = (
            select(func.count())
            .select_from(RoleMenuORM)
            .where(
                RoleMenuORM.role_id == role_id,
                RoleMenuORM.menu_id == menu_id,
            )
        )
        count_result = await self._session.execute(check_stmt)
        if int(count_result.scalar() or 0) == 0:
            return False
        await self._session.execute(
            delete(RoleMenuORM).where(
                RoleMenuORM.role_id == role_id,
                RoleMenuORM.menu_id == menu_id,
            ),
        )
        await self._session.flush()
        return True

    async def get_menu_ids_by_role_ids(self, role_ids: set[UUID]) -> set[UUID]:
        """按角色 ID 集合查询已分配的菜单 ID 集合."""

        if not role_ids:
            return set()
        stmt = select(RoleMenuORM.menu_id).where(
            RoleMenuORM.role_id.in_(role_ids),
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def get_menus_by_ids(self, menu_ids: set[UUID]) -> list[Menu]:
        """按 ID 集合查询启用状态的菜单实体."""

        if not menu_ids:
            return []
        stmt = (
            select(MenuORM)
            .where(
                MenuORM.id.in_(menu_ids),
                MenuORM.status == MenuStatus.ACTIVE.value,
            )
            .order_by(MenuORM.sort_order, MenuORM.title)
        )
        result = await self._session.execute(stmt)
        return [_orm_to_menu(orm) for orm in result.scalars().all()]


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _orm_to_menu(orm: MenuORM) -> Menu:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return Menu(
        id=orm.id,
        parent_id=orm.parent_id,
        menu_type=MenuType(orm.menu_type),
        title=orm.title,
        name=orm.name,
        path=orm.path,
        component=orm.component,
        icon=orm.icon,
        sort_order=orm.sort_order,
        visible=orm.visible,
        status=MenuStatus(orm.status),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _menu_to_orm(menu: Menu) -> MenuORM:
    """领域实体 → ORM 模型转换."""

    return MenuORM(
        id=menu.id,
        parent_id=menu.parent_id,
        menu_type=menu.menu_type.value,
        title=menu.title,
        name=menu.name,
        path=menu.path,
        component=menu.component,
        icon=menu.icon,
        sort_order=menu.sort_order,
        visible=menu.visible,
        status=menu.status.value,
        created_at=menu.created_at,
        updated_at=menu.updated_at,
        created_by=menu.created_by,
        updated_by=menu.updated_by,
    )
