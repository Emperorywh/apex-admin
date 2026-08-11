"""审计模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

审计模块提供:
  - 操作审计表（``audit_logs``）与登录日志表（``login_logs``）。
  - ``AuditPort``（同事务提交）、``LoginLogPort``、``SecurityLogPort``。
  - 审计查询面: ``AuditQueryPort``、``LoginLogQueryPort``、``AuditRetentionPort``。
  - 变更差异字段白名单机制、显示名快照、审计不可变约束。
  - 审计查询与导出 API（SPEC 18.3）。
  - 日志保留治理命令（SPEC 18.4 / 25.3）。

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ManagementCommand, ModuleDefinition
from app.modules.audit.errors import AUDIT_LOG_NOT_FOUND, AUDIT_LOGIN_LOG_NOT_FOUND
from app.modules.audit.port import AuditPort, LoginLogPort, SecurityLogPort
from app.modules.audit.router import router as audit_router  # noqa: E402

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "audit"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "audit"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
#: Alembic 的 version_locations 以 CWD 为基准解析，
#: 测试和 CLI 均在项目根目录运行。
ALEMBIC_VERSION_DIR = "src/app/modules/audit/migrations"

#: 权限点 — SPEC 5.5 / 23.5: 所有管理接口具有权限点。
PERMISSION_AUDIT_LOG_READ = "audit:log:read"
PERMISSION_AUDIT_LOG_EXPORT = "audit:log:export"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
#: 导出操作本身的审计动作（SPEC 18.3: 导出行为写入新的审计事件）。
AUDIT_LOG_EXPORT = "audit.log.export"
AUDIT_LOGIN_LOG_EXPORT = "audit.login_log.export"


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
    routers=(audit_router,),
    permission_codes=(
        PERMISSION_AUDIT_LOG_READ,
        PERMISSION_AUDIT_LOG_EXPORT,
    ),
    error_codes=(
        AUDIT_LOG_NOT_FOUND,
        AUDIT_LOGIN_LOG_NOT_FOUND,
    ),
    audit_actions=(
        AUDIT_LOG_EXPORT,
        AUDIT_LOGIN_LOG_EXPORT,
    ),
    protected_resource_types=(),
    initializers=(),  # 审计表由迁移创建，无需种子数据。
    management_commands=(
        ManagementCommand(
            name="audit cleanup",
            description="审计日志保留清理（默认 dry-run，--apply 执行删除）",
        ),
    ),
    event_handlers=(),
    event_codes=(),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
