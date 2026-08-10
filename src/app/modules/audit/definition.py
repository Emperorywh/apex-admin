"""审计模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

审计模块提供:
  - 操作审计表（``audit_logs``）与登录日志表（``login_logs``）。
  - ``AuditPort``（同事务提交）、``LoginLogPort``、``SecurityLogPort``。
  - 变更差异字段白名单机制、显示名快照、审计不可变约束。

本任务只建模块与 Port，业务接线由后续任务完成。模块无 Router、
权限点、错误码、审计动作和初始化器（审计模块是基础设施提供者，
具体审计动作由消费模块声明）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.audit.port import AuditPort, LoginLogPort, SecurityLogPort

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "audit"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "audit"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
#: Alembic 的 version_locations 以 CWD 为基准解析，
#: 测试和 CLI 均在项目根目录运行。
ALEMBIC_VERSION_DIR = "src/app/modules/audit/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    # SPEC 5.5: 模块公开的 Application Port。
    # 审计模块向其他模块公开 AuditPort、LoginLogPort 和 SecurityLogPort，
    # 其他模块通过依赖注入获得这些 Port 的实现。
    application_ports=(AuditPort, LoginLogPort, SecurityLogPort),
    required_dependencies=(),
    optional_dependencies=(),
    routers=(),  # 审计查询 API 由 TASK-024 实现。
    permission_codes=(),  # 审计查询权限由 TASK-024 声明。
    error_codes=(),
    audit_actions=(),  # 具体审计动作由消费模块（user/auth 等）声明。
    protected_resource_types=(),
    initializers=(),  # 审计表由迁移创建，无需种子数据。
    management_commands=(),  # 保留与清理命令由 TASK-024 实现。
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
