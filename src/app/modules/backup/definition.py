"""备份模块 ModuleDefinition — SPEC 5.5 / 27.1 / 27.2 / 27.3.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

备份模块为 CLI 运维命令的支撑服务，无 API 路由、无数据库迁移、无权限点，
仅提供管理命令声明（``backup create`` / ``backup verify``）。
"""

from __future__ import annotations

from app.core.modules.definition import ManagementCommand, ModuleDefinition

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "backup"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "backup"

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    application_ports=(),
    required_dependencies=(),
    optional_dependencies=("file",),
    routers=(),
    permission_codes=(),
    error_codes=(),
    audit_actions=(),
    protected_resource_types=(),
    initializers=(),
    management_commands=(
        ManagementCommand(
            name="backup create",
            description="创建数据库逻辑全量备份（pg_dump）与 READY 文件清单",
        ),
        ManagementCommand(
            name="backup verify",
            description="隔离库恢复演练——迁移版本/数据完整性/文件一致性检查",
        ),
    ),
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=None,
)
