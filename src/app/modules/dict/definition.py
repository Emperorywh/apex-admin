"""数据字典模块 ModuleDefinition — SPEC 5.5 / 17.1 / 17.2.

此文件公开数据字典模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``dict``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - 公开 Application Port（``ReferenceRegistryPort`` 供业务模块登记引用）
  - 幂等初始化器（种子字典）
  - Alembic 迁移版本目录

SPEC 17.1: 字典类型管理（CRUD/启用禁用/编码唯一/删除保护/引用登记 Port）。
SPEC 17.2: 字典项管理（CRUD/启用禁用/显示文本/稳定值/排序/扩展元数据/审计）。

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.dict.errors import (
    DICT_ITEM_ALREADY_ACTIVE,
    DICT_ITEM_ALREADY_DISABLED,
    DICT_ITEM_DUPLICATE_VALUE,
    DICT_ITEM_NOT_FOUND,
    DICT_TYPE_ALREADY_ACTIVE,
    DICT_TYPE_ALREADY_DISABLED,
    DICT_TYPE_DISABLED,
    DICT_TYPE_DUPLICATE_CODE,
    DICT_TYPE_NOT_FOUND,
    DICT_TYPE_REFERENCED,
)
from app.modules.dict.initializers import DictSeedInitializer
from app.modules.dict.port import ReferenceRegistryPort
from app.modules.dict.router import router as dict_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "dict"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "dict"

#: 权限点 — SPEC 5.5 / 23.5: 所有管理接口具有权限点。
PERMISSION_DICT_TYPE_READ = "dict:type:read"
PERMISSION_DICT_TYPE_WRITE = "dict:type:write"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
AUDIT_DICT_TYPE_CREATE = "dict.type.create"
AUDIT_DICT_TYPE_UPDATE = "dict.type.update"
AUDIT_DICT_TYPE_ENABLE = "dict.type.enable"
AUDIT_DICT_TYPE_DISABLE = "dict.type.disable"
AUDIT_DICT_TYPE_DELETE = "dict.type.delete"
AUDIT_DICT_ITEM_CREATE = "dict.item.create"
AUDIT_DICT_ITEM_UPDATE = "dict.item.update"
AUDIT_DICT_ITEM_ENABLE = "dict.item.enable"
AUDIT_DICT_ITEM_DISABLE = "dict.item.disable"
AUDIT_DICT_ITEM_DELETE = "dict.item.delete"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/dict/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    # SPEC 5.5: 模块公开的 Application Port。
    # ReferenceRegistryPort 供业务模块登记对字典类型的引用，
    # 删除字典类型时检查引用登记（SPEC 17.1: 删除保护）。
    application_ports=(ReferenceRegistryPort,),
    required_dependencies=("audit",),  # 审计写入
    optional_dependencies=(),
    routers=(dict_router,),
    permission_codes=(
        PERMISSION_DICT_TYPE_READ,
        PERMISSION_DICT_TYPE_WRITE,
    ),
    error_codes=(
        DICT_TYPE_NOT_FOUND,
        DICT_TYPE_DUPLICATE_CODE,
        DICT_TYPE_ALREADY_DISABLED,
        DICT_TYPE_ALREADY_ACTIVE,
        DICT_TYPE_REFERENCED,
        DICT_TYPE_DISABLED,
        DICT_ITEM_NOT_FOUND,
        DICT_ITEM_DUPLICATE_VALUE,
        DICT_ITEM_ALREADY_DISABLED,
        DICT_ITEM_ALREADY_ACTIVE,
    ),
    audit_actions=(
        AUDIT_DICT_TYPE_CREATE,
        AUDIT_DICT_TYPE_UPDATE,
        AUDIT_DICT_TYPE_ENABLE,
        AUDIT_DICT_TYPE_DISABLE,
        AUDIT_DICT_TYPE_DELETE,
        AUDIT_DICT_ITEM_CREATE,
        AUDIT_DICT_ITEM_UPDATE,
        AUDIT_DICT_ITEM_ENABLE,
        AUDIT_DICT_ITEM_DISABLE,
        AUDIT_DICT_ITEM_DELETE,
    ),
    protected_resource_types=("dict_type", "dict_item"),
    initializers=(DictSeedInitializer(),),
    management_commands=(),
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
