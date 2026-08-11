"""系统配置模块 ModuleDefinition — SPEC 5.5 / 16.1 / 16.2.

此文件公开系统配置模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``sysconfig``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - Alembic 迁移版本目录

SPEC 16.1: 配置项管理（CRUD/启用禁用/分组管理/类型校验/加密/审计/核心安全保护）。
SPEC 16.2: 统一配置读取服务（声明式键白名单/越键读取拒绝）。
SPEC 23.2: 敏感配置加密密钥管理与轮换。
"""

from __future__ import annotations

from app.core.modules.definition import ManagementCommand, ModuleDefinition
from app.modules.sysconfig.errors import (
    SYSCONFIG_ALREADY_ACTIVE,
    SYSCONFIG_ALREADY_DISABLED,
    SYSCONFIG_CORE_SECURITY_PROTECTED,
    SYSCONFIG_DUPLICATE_KEY,
    SYSCONFIG_INVALID_TYPE,
    SYSCONFIG_KEY_NOT_DECLARED,
    SYSCONFIG_NOT_FOUND,
    SYSCONFIG_VALUE_TYPE_MISMATCH,
)
from app.modules.sysconfig.router import router as sysconfig_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "sysconfig"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "sysconfig"

#: 权限点 — SPEC 5.5 / 23.5: 所有管理接口具有权限点。
PERMISSION_CONFIG_READ = "sysconfig:config:read"
PERMISSION_CONFIG_WRITE = "sysconfig:config:write"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
AUDIT_CONFIG_CREATE = "sysconfig.create"
AUDIT_CONFIG_UPDATE = "sysconfig.update"
AUDIT_CONFIG_ENABLE = "sysconfig.enable"
AUDIT_CONFIG_DISABLE = "sysconfig.disable"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/sysconfig/migrations"

# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    application_ports=(),
    required_dependencies=("audit",),  # 审计写入
    optional_dependencies=(),
    routers=(sysconfig_router,),
    permission_codes=(
        PERMISSION_CONFIG_READ,
        PERMISSION_CONFIG_WRITE,
    ),
    error_codes=(
        SYSCONFIG_NOT_FOUND,
        SYSCONFIG_ALREADY_DISABLED,
        SYSCONFIG_ALREADY_ACTIVE,
        SYSCONFIG_DUPLICATE_KEY,
        SYSCONFIG_INVALID_TYPE,
        SYSCONFIG_VALUE_TYPE_MISMATCH,
        SYSCONFIG_CORE_SECURITY_PROTECTED,
        SYSCONFIG_KEY_NOT_DECLARED,
    ),
    audit_actions=(
        AUDIT_CONFIG_CREATE,
        AUDIT_CONFIG_UPDATE,
        AUDIT_CONFIG_ENABLE,
        AUDIT_CONFIG_DISABLE,
    ),
    protected_resource_types=("config",),
    initializers=(),
    management_commands=(
        ManagementCommand(
            name="sysconfig re-encrypt",
            description="敏感配置加密密钥轮换重加密（SPEC 23.2 双密钥短期切换）",
        ),
    ),
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
