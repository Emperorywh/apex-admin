"""示例模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配。"

此文件公开示例模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``example``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - 幂等初始化器
  - 事务内事件处理器和事件编码
  - Alembic 迁移版本目录

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.example.errors import EXAMPLE_CONFLICT, EXAMPLE_NOT_FOUND
from app.modules.example.initializer import ExampleInitializer
from app.modules.example.router import router as example_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "example"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "example"

#: 权限点 — SPEC 5.5: 小写三段或多段形式。
PERMISSION_EXAMPLE_ITEM_READ = "example:item:read"
PERMISSION_EXAMPLE_ITEM_WRITE = "example:item:write"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
AUDIT_ITEM_CREATE = "example.item.create"
AUDIT_ITEM_UPDATE = "example.item.update"
AUDIT_ITEM_DELETE = "example.item.delete"

#: 事件编码 — SPEC 5.7: ``<MODULE>.<EVENT>``。
EVENT_ITEM_CREATED = "EXAMPLE.ITEM_CREATED"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
#: Alembic 的 version_locations 以 CWD 为基准解析，
#: 测试和 CLI 均在项目根目录运行。
ALEMBIC_VERSION_DIR = "src/app/modules/example/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    # SPEC 5.5: 模块公开的 Application Port。
    # 当前示例模块的 Port（ExampleItemRepository）在模块内部使用，
    # 未作为跨模块公开 Port 声明。后续模块需跨模块协作时在此声明。
    application_ports=(),
    required_dependencies=(),
    optional_dependencies=(),
    routers=(example_router,),
    permission_codes=(
        PERMISSION_EXAMPLE_ITEM_READ,
        PERMISSION_EXAMPLE_ITEM_WRITE,
    ),
    error_codes=(
        EXAMPLE_NOT_FOUND,
        EXAMPLE_CONFLICT,
    ),
    audit_actions=(
        AUDIT_ITEM_CREATE,
        AUDIT_ITEM_UPDATE,
        AUDIT_ITEM_DELETE,
    ),
    protected_resource_types=("example_item",),
    initializers=(ExampleInitializer(),),
    management_commands=(),
    event_handlers=(),  # 处理器在 Use Case 装配时注入，此处声明事件编码。
    event_codes=(EVENT_ITEM_CREATED,),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
