"""菜单模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

此文件公开菜单模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``menu``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - Alembic 迁移版本目录

SPEC 15.1: 菜单资源（CRUD/树/启停/层级排序/角色菜单分配）。
SPEC 15.2: 当前用户菜单树与按钮权限编码端点。
SPEC 23.5: 菜单可见性不承担授权。

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.menu.errors import (
    MENU_ALREADY_ACTIVE,
    MENU_ALREADY_DISABLED,
    MENU_CYCLE_DETECTED,
    MENU_HAS_CHILDREN,
    MENU_INVALID_PARENT,
    MENU_INVALID_TYPE,
    MENU_NOT_FOUND,
)
from app.modules.menu.router import router as menu_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "menu"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "menu"

#: 权限点 — SPEC 5.5 / 23.5: 所有管理接口具有权限点。
PERMISSION_MENU_READ = "menu:menu:read"
PERMISSION_MENU_WRITE = "menu:menu:write"
PERMISSION_ROLE_MENU_WRITE = "menu:role_menu:write"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
AUDIT_MENU_CREATE = "menu.create"
AUDIT_MENU_UPDATE = "menu.update"
AUDIT_MENU_ENABLE = "menu.enable"
AUDIT_MENU_DISABLE = "menu.disable"
AUDIT_MENU_ADJUST_HIERARCHY = "menu.adjust_hierarchy"
AUDIT_MENU_DELETE = "menu.delete"
AUDIT_ROLE_ASSIGN_MENUS = "menu.role.assign_menus"
AUDIT_ROLE_REMOVE_MENU = "menu.role.remove_menu"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/menu/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    application_ports=(),
    required_dependencies=("audit", "rbac"),  # 审计写入 + 用户角色权限查询
    optional_dependencies=(),
    routers=(menu_router,),
    permission_codes=(
        PERMISSION_MENU_READ,
        PERMISSION_MENU_WRITE,
        PERMISSION_ROLE_MENU_WRITE,
    ),
    error_codes=(
        MENU_NOT_FOUND,
        MENU_ALREADY_DISABLED,
        MENU_ALREADY_ACTIVE,
        MENU_CYCLE_DETECTED,
        MENU_INVALID_PARENT,
        MENU_HAS_CHILDREN,
        MENU_INVALID_TYPE,
    ),
    audit_actions=(
        AUDIT_MENU_CREATE,
        AUDIT_MENU_UPDATE,
        AUDIT_MENU_ENABLE,
        AUDIT_MENU_DISABLE,
        AUDIT_MENU_ADJUST_HIERARCHY,
        AUDIT_MENU_DELETE,
        AUDIT_ROLE_ASSIGN_MENUS,
        AUDIT_ROLE_REMOVE_MENU,
    ),
    protected_resource_types=("menu", "role_menu"),
    initializers=(),
    management_commands=(),
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
