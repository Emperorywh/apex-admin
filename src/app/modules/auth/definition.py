"""认证模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

此文件公开认证模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``auth``
  - 错误码
  - 事务内事件处理器编码（USER.DISABLED / USER.PASSWORD_RESET_BY_ADMIN 的会话吊销）
  - Router 列表
  - Alembic 迁移版本目录

认证模块声明对 ``audit`` 和 ``identity`` 的必需依赖:
  - ``audit``: 登录日志和安全日志 Port。
  - ``identity``: 用户认证信息 Port（UserAuthPort），查询用户状态和密码哈希。

SPEC 12.3: 事件处理器在 identity 模块的禁用/重置密码 Use Case 事务内执行，
吊销该用户全部会话（SPEC 5.7: 同提交、同回滚）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.auth.errors import (
    AUTH_INVALID_CREDENTIALS,
    AUTH_REFRESH_FAILED,
    AUTH_SESSION_NOT_FOUND,
)
from app.modules.auth.handlers import (
    RevokeSessionsOnPasswordReset,
    RevokeSessionsOnUserDisabled,
)
from app.modules.auth.router import router as auth_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "auth"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "auth"

#: 受保护资源类型 — 会话。
RESOURCE_TYPE_SESSION = "session"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/auth/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    # SPEC 5.5: auth 模块不向外公开 Port（其他模块通过事件向 auth 发送指令）。
    application_ports=(),
    required_dependencies=("audit", "identity"),  # 登录日志 + 用户认证信息
    optional_dependencies=(),
    routers=(auth_router,),
    permission_codes=(),  # RBAC 在 TASK-015/016 实现。
    error_codes=(
        AUTH_INVALID_CREDENTIALS,
        AUTH_SESSION_NOT_FOUND,
        AUTH_REFRESH_FAILED,
    ),
    audit_actions=(),  # 登录日志使用 LoginLogPort，不走 AuditPort。
    protected_resource_types=(RESOURCE_TYPE_SESSION,),
    initializers=(),
    management_commands=(),  # auth create-admin 在后续任务实现。
    event_handlers=(
        RevokeSessionsOnUserDisabled(),
        RevokeSessionsOnPasswordReset(),
    ),
    event_codes=(),  # auth 模块不产生事件。
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
