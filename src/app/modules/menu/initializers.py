"""菜单种子初始化器 — SPEC 8.5 / 15.1.

SPEC 8.5:
  - 初始化器使用稳定自然键执行幂等 upsert。
  - 初始化过程可重复执行且不会创建重复数据。
  - 初始化器只能写入本模块拥有的数据。

SPEC 15.1: 菜单为树形实体，种子菜单提供后台管理基础导航结构。

与 TASK-027 的 ``admin sync-seeds`` 协同——初始化器提供种子数据定义，
``admin sync-seeds`` 命令调用初始化框架执行全部种子。

初始化器使用确定性 UUID（``uuid5``）作为主键，``ON CONFLICT (id) DO UPDATE``
实现幂等 upsert，保证重复执行不产生重复菜单。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.initialization.framework import Initializer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── 种子菜单定义 ────────────────────────────────────────────────────────────
#
# 每个种子菜单通过稳定编码生成确定性 UUID 作为主键。
# 菜单类型: directory（目录）用于组织层级，page（页面）对应前端路由。

_SEED_MENUS: tuple[dict[str, str | int | None], ...] = (
    # ── 系统管理（一级目录）──
    {
        "code": "system",
        "parent_code": None,
        "menu_type": "directory",
        "title": "系统管理",
        "name": "System",
        "path": "/system",
        "component": None,
        "icon": "setting",
        "sort_order": 100,
    },
    # ── 系统管理子菜单 ──
    {
        "code": "system_user",
        "parent_code": "system",
        "menu_type": "page",
        "title": "用户管理",
        "name": "SystemUser",
        "path": "/system/users",
        "component": "system/user/index",
        "icon": "user",
        "sort_order": 10,
    },
    {
        "code": "system_role",
        "parent_code": "system",
        "menu_type": "page",
        "title": "角色管理",
        "name": "SystemRole",
        "path": "/system/roles",
        "component": "system/role/index",
        "icon": "team",
        "sort_order": 20,
    },
    {
        "code": "system_menu",
        "parent_code": "system",
        "menu_type": "page",
        "title": "菜单管理",
        "name": "SystemMenu",
        "path": "/system/menus",
        "component": "system/menu/index",
        "icon": "menu",
        "sort_order": 30,
    },
    {
        "code": "system_dept",
        "parent_code": "system",
        "menu_type": "page",
        "title": "部门管理",
        "name": "SystemDept",
        "path": "/system/departments",
        "component": "system/dept/index",
        "icon": "apartment",
        "sort_order": 40,
    },
    {
        "code": "system_dict",
        "parent_code": "system",
        "menu_type": "page",
        "title": "字典管理",
        "name": "SystemDict",
        "path": "/system/dict",
        "component": "system/dict/index",
        "icon": "book",
        "sort_order": 50,
    },
    {
        "code": "system_config",
        "parent_code": "system",
        "menu_type": "page",
        "title": "系统配置",
        "name": "SystemConfig",
        "path": "/system/config",
        "component": "system/config/index",
        "icon": "tool",
        "sort_order": 60,
    },
    {
        "code": "system_audit",
        "parent_code": "system",
        "menu_type": "page",
        "title": "审计日志",
        "name": "SystemAudit",
        "path": "/system/audit",
        "component": "system/audit/index",
        "icon": "file-search",
        "sort_order": 70,
    },
)


def _menu_deterministic_id(code: str) -> str:
    """为种子菜单生成确定性 UUID — 保证幂等插入."""

    from uuid import NAMESPACE_URL, uuid5

    return str(uuid5(NAMESPACE_URL, f"apex:menu:{code}"))


class MenuSeedInitializer(Initializer):
    """菜单种子幂等初始化器 — SPEC 8.5 / 15.1.

    以稳定编码生成的确定性 UUID（稳定主键）执行幂等 upsert，
    保证重复执行不产生重复数据。
    与 TASK-027 的 ``admin sync-seeds`` 命令协同。
    """

    @property
    def code(self) -> str:
        return "MENU.SEED_SYSTEM_MENUS"

    async def initialize(self, session: AsyncSession) -> None:
        """对每个种子菜单执行幂等 upsert.

        SPEC 8.5: 使用 ``ON CONFLICT (id) DO UPDATE`` 实现幂等 upsert，
        以稳定编码生成的确定性 UUID 作为主键判断依据。

        第一轮 upsert 全部菜单（parent_id 为 NULL 或临时指向自身），
        第二轮更新 parent_id 为正确的父菜单 ID。两轮方式确保
        父子顺序不影响 upsert 正确性。
        """

        from sqlalchemy import text

        # 第一轮: upsert 全部菜单，parent_id 暂设为 NULL
        for menu in _SEED_MENUS:
            menu_id = _menu_deterministic_id(str(menu["code"]))
            await session.execute(
                text(
                    "INSERT INTO menu_menus "
                    "(id, parent_id, menu_type, title, name, path, "
                    "component, icon, sort_order, visible, status, "
                    "created_at, updated_at) "
                    "VALUES "
                    "(:id, NULL, :menu_type, :title, :name, :path, "
                    ":component, :icon, :sort_order, TRUE, 'active', "
                    "NOW() AT TIME ZONE 'UTC', "
                    "NOW() AT TIME ZONE 'UTC') "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "  menu_type = EXCLUDED.menu_type, "
                    "  title = EXCLUDED.title, "
                    "  name = EXCLUDED.name, "
                    "  path = EXCLUDED.path, "
                    "  component = EXCLUDED.component, "
                    "  icon = EXCLUDED.icon, "
                    "  sort_order = EXCLUDED.sort_order, "
                    "  updated_at = NOW() AT TIME ZONE 'UTC'",
                ),
                {
                    "id": menu_id,
                    "menu_type": str(menu["menu_type"]),
                    "title": str(menu["title"]),
                    "name": menu["name"],
                    "path": menu["path"],
                    "component": menu["component"],
                    "icon": menu["icon"],
                    "sort_order": int(menu["sort_order"]),  # type: ignore[arg-type]
                },
            )

        # 第二轮: 更新 parent_id 为正确的父菜单 ID
        for menu in _SEED_MENUS:
            parent_code = menu["parent_code"]
            if parent_code is None:
                continue
            menu_id = _menu_deterministic_id(str(menu["code"]))
            parent_id = _menu_deterministic_id(str(parent_code))
            await session.execute(
                text(
                    "UPDATE menu_menus SET parent_id = :parent_id, "
                    "updated_at = NOW() AT TIME ZONE 'UTC' "
                    "WHERE id = :id",
                ),
                {"id": menu_id, "parent_id": parent_id},
            )
