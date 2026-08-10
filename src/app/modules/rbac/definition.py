"""RBAC 模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

此文件公开 RBAC 模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``rbac``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - 公开 Application Port（``UserRbacPort`` 供 auth 模块 TASK-016 查询有效权限）
  - 管理命令（``auth sync-permissions`` 由本模块逻辑支撑）
  - Alembic 迁移版本目录

SPEC 13.1 / 13.2 / 25.2:
  - 权限点编码小写多段，来自各模块 ModuleDefinition 声明。
  - 内置角色保护规则。
  - ``auth sync-permissions`` 幂等同步权限点目录。

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ManagementCommand, ModuleDefinition
from app.modules.rbac.errors import (
    RBAC_BUILTIN_ROLE_PROTECTED,
    RBAC_PERMISSION_NOT_FOUND,
    RBAC_ROLE_ALREADY_ACTIVE,
    RBAC_ROLE_ALREADY_DISABLED,
    RBAC_ROLE_ALREADY_EXISTS,
    RBAC_ROLE_HAS_USERS,
    RBAC_ROLE_NOT_FOUND,
    RBAC_USER_ROLE_ALREADY_ASSIGNED,
    RBAC_USER_ROLE_NOT_ASSIGNED,
)
from app.modules.rbac.initializers import BuiltinRolesInitializer
from app.modules.rbac.port import UserRbacPort
from app.modules.rbac.router import router as rbac_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "rbac"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "rbac"

#: 权限点 — SPEC 5.5 / 23.5: 所有管理接口具有权限点。
PERMISSION_ROLE_READ = "rbac:role:read"
PERMISSION_ROLE_WRITE = "rbac:role:write"
PERMISSION_PERMISSION_READ = "rbac:permission:read"
PERMISSION_ASSIGNMENT_WRITE = "rbac:assignment:write"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
AUDIT_ROLE_CREATE = "rbac.role.create"
AUDIT_ROLE_UPDATE = "rbac.role.update"
AUDIT_ROLE_ENABLE = "rbac.role.enable"
AUDIT_ROLE_DISABLE = "rbac.role.disable"
AUDIT_ROLE_DELETE = "rbac.role.delete"
AUDIT_ROLE_ASSIGN_PERMISSIONS = "rbac.role.assign_permissions"
AUDIT_USER_ROLE_ASSIGN = "rbac.user_role.assign"
AUDIT_USER_ROLE_REMOVE = "rbac.user_role.remove"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/rbac/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    # SPEC 5.5: 模块公开的 Application Port。
    # UserRbacPort 供 auth 模块（TASK-016）跨模块查询用户有效权限集。
    application_ports=(UserRbacPort,),
    required_dependencies=("audit", "identity"),  # 审计写入 + 用户存在性校验
    optional_dependencies=(),
    routers=(rbac_router,),
    permission_codes=(
        PERMISSION_ROLE_READ,
        PERMISSION_ROLE_WRITE,
        PERMISSION_PERMISSION_READ,
        PERMISSION_ASSIGNMENT_WRITE,
    ),
    error_codes=(
        RBAC_ROLE_NOT_FOUND,
        RBAC_ROLE_ALREADY_EXISTS,
        RBAC_ROLE_ALREADY_DISABLED,
        RBAC_ROLE_ALREADY_ACTIVE,
        RBAC_PERMISSION_NOT_FOUND,
        RBAC_BUILTIN_ROLE_PROTECTED,
        RBAC_USER_ROLE_ALREADY_ASSIGNED,
        RBAC_USER_ROLE_NOT_ASSIGNED,
        RBAC_ROLE_HAS_USERS,
    ),
    audit_actions=(
        AUDIT_ROLE_CREATE,
        AUDIT_ROLE_UPDATE,
        AUDIT_ROLE_ENABLE,
        AUDIT_ROLE_DISABLE,
        AUDIT_ROLE_DELETE,
        AUDIT_ROLE_ASSIGN_PERMISSIONS,
        AUDIT_USER_ROLE_ASSIGN,
        AUDIT_USER_ROLE_REMOVE,
    ),
    protected_resource_types=("role",),
    initializers=(BuiltinRolesInitializer(),),
    management_commands=(
        ManagementCommand(
            name="auth sync-permissions",
            description="幂等同步 G2 启用模块声明的权限点到权限目录",
        ),
    ),
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
