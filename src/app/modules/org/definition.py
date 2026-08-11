"""组织模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

此文件公开组织模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``org``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - Alembic 迁移版本目录
  - 事务内事件处理器（USER.DISABLED → 清理用户组织关系）

SPEC 14.1: 部门管理为 org 模块的树形实体。
SPEC 14.2: 岗位管理（CRUD + 启停 + 用户分配移除）。
SPEC 14.3: 用户组织关系（主部门 + 离职/禁用事件处理）。

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.org.errors import (
    ORG_DEPT_ALREADY_ACTIVE,
    ORG_DEPT_ALREADY_DISABLED,
    ORG_DEPT_ALREADY_EXISTS,
    ORG_DEPT_CYCLE_DETECTED,
    ORG_DEPT_DISABLED,
    ORG_DEPT_HAS_CHILDREN,
    ORG_DEPT_HAS_USERS,
    ORG_DEPT_INVALID_PARENT,
    ORG_DEPT_NOT_FOUND,
    ORG_POST_ALREADY_ACTIVE,
    ORG_POST_ALREADY_DISABLED,
    ORG_POST_ALREADY_EXISTS,
    ORG_POST_DISABLED,
    ORG_POST_HAS_USERS,
    ORG_POST_NOT_FOUND,
    ORG_USER_ALREADY_HAS_DEPARTMENT,
    ORG_USER_DEPT_NOT_FOUND,
    ORG_USER_POST_DUPLICATE,
    ORG_USER_POST_NOT_FOUND,
)
from app.modules.org.handlers import ClearUserOrgRelationsOnDisabled
from app.modules.org.port import UserOrgPort
from app.modules.org.router import router as org_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "org"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "org"

#: 权限点 — SPEC 5.5 / 23.5: 所有管理接口具有权限点。
PERMISSION_DEPT_READ = "org:dept:read"
PERMISSION_DEPT_WRITE = "org:dept:write"
PERMISSION_POST_READ = "org:post:read"
PERMISSION_POST_WRITE = "org:post:write"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
AUDIT_DEPT_CREATE = "org.dept.create"
AUDIT_DEPT_UPDATE = "org.dept.update"
AUDIT_DEPT_ENABLE = "org.dept.enable"
AUDIT_DEPT_DISABLE = "org.dept.disable"
AUDIT_DEPT_ADJUST_HIERARCHY = "org.dept.adjust_hierarchy"
AUDIT_DEPT_SET_LEADER = "org.dept.set_leader"
AUDIT_DEPT_DELETE = "org.dept.delete"
AUDIT_POST_CREATE = "org.post.create"
AUDIT_POST_UPDATE = "org.post.update"
AUDIT_POST_ENABLE = "org.post.enable"
AUDIT_POST_DISABLE = "org.post.disable"
AUDIT_POST_DELETE = "org.post.delete"
AUDIT_USER_ASSIGN_DEPARTMENT = "org.user.assign_department"
AUDIT_USER_REMOVE_DEPARTMENT = "org.user.remove_department"
AUDIT_USER_ASSIGN_POST = "org.user.assign_post"
AUDIT_USER_REMOVE_POST = "org.user.remove_post"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/org/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    application_ports=(UserOrgPort,),
    required_dependencies=("audit", "identity"),  # 审计写入 + 用户存在性校验
    optional_dependencies=(),
    routers=(org_router,),
    permission_codes=(
        PERMISSION_DEPT_READ,
        PERMISSION_DEPT_WRITE,
        PERMISSION_POST_READ,
        PERMISSION_POST_WRITE,
    ),
    error_codes=(
        ORG_DEPT_NOT_FOUND,
        ORG_DEPT_ALREADY_EXISTS,
        ORG_DEPT_ALREADY_DISABLED,
        ORG_DEPT_ALREADY_ACTIVE,
        ORG_DEPT_HAS_CHILDREN,
        ORG_DEPT_HAS_USERS,
        ORG_DEPT_CYCLE_DETECTED,
        ORG_DEPT_INVALID_PARENT,
        ORG_POST_NOT_FOUND,
        ORG_POST_ALREADY_EXISTS,
        ORG_POST_ALREADY_DISABLED,
        ORG_POST_ALREADY_ACTIVE,
        ORG_POST_HAS_USERS,
        ORG_POST_DISABLED,
        ORG_DEPT_DISABLED,
        ORG_USER_ALREADY_HAS_DEPARTMENT,
        ORG_USER_POST_DUPLICATE,
        ORG_USER_DEPT_NOT_FOUND,
        ORG_USER_POST_NOT_FOUND,
    ),
    audit_actions=(
        AUDIT_DEPT_CREATE,
        AUDIT_DEPT_UPDATE,
        AUDIT_DEPT_ENABLE,
        AUDIT_DEPT_DISABLE,
        AUDIT_DEPT_ADJUST_HIERARCHY,
        AUDIT_DEPT_SET_LEADER,
        AUDIT_DEPT_DELETE,
        AUDIT_POST_CREATE,
        AUDIT_POST_UPDATE,
        AUDIT_POST_ENABLE,
        AUDIT_POST_DISABLE,
        AUDIT_POST_DELETE,
        AUDIT_USER_ASSIGN_DEPARTMENT,
        AUDIT_USER_REMOVE_DEPARTMENT,
        AUDIT_USER_ASSIGN_POST,
        AUDIT_USER_REMOVE_POST,
    ),
    protected_resource_types=("department", "post", "user_org"),
    initializers=(),
    management_commands=(),
    event_handlers=(ClearUserOrgRelationsOnDisabled(),),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
