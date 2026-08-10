"""身份用户模块 ModuleDefinition — SPEC 5.5.

SPEC 5.5: "每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配"。

此文件公开身份用户模块的全部声明信息（SPEC 5.5）:
  - 模块编码 ``identity``
  - 权限点、错误码、审计动作、受保护资源类型
  - Router 列表
  - 事务内事件编码（处理器由 auth 模块 TASK-013 注册）
  - Alembic 迁移版本目录

SPEC 11.3 删除策略语义（写入模块文档）:
  - 物理删除受审计记录保护——已产生审计记录的用户不得物理删除
    （SPEC 11.3: "已产生审计记录的用户不得因物理删除导致审计信息失真"）。
  - 默认优先采用禁用（``disable``），而不是直接删除用户
    （SPEC 11.3: "默认优先采用禁用或注销，而不是直接删除用户"）。
  - 用户名称发生变化时，历史审计记录通过显示名快照仍能识别当时操作者
    （SPEC 11.3: "用户名称发生变化时，历史审计记录仍能识别当时操作者"）。
  - username 全局唯一且不可变更——变更用户名会影响历史审计可追溯性，
    显示名快照机制保证历史记录可读。

导入此模块时自动注册错误码到框架注册表（通过 ``errors.py``）。
"""

from __future__ import annotations

from app.core.modules.definition import ModuleDefinition
from app.modules.identity.errors import (
    USER_ALREADY_ACTIVE,
    USER_ALREADY_DISABLED,
    USER_ALREADY_EXISTS,
    USER_HAS_AUDIT_RECORDS,
    USER_INVALID_OLD_PASSWORD,
    USER_NOT_FOUND,
)
from app.modules.identity.router import router as identity_router

# ── 声明常量 ────────────────────────────────────────────────────────────────

#: 模块编码 — 全局唯一且稳定（SPEC 5.5）。
MODULE_CODE = "identity"

#: API Tag — 用于 OpenAPI 分组（SPEC 9.6），全局唯一。
MODULE_API_TAG = "identity"

#: 权限点 — SPEC 5.5: 小写三段或多段形式。
#: SPEC 23.5: "所有管理接口具有权限点"。
PERMISSION_USER_READ = "system:user:read"
PERMISSION_USER_WRITE = "system:user:write"

#: 审计动作 — SPEC 18.2: 记录操作模块和动作。
AUDIT_USER_CREATE = "identity.user.create"
AUDIT_USER_UPDATE = "identity.user.update"
AUDIT_USER_ENABLE = "identity.user.enable"
AUDIT_USER_DISABLE = "identity.user.disable"
AUDIT_USER_RESET_PASSWORD = "identity.user.reset_password"
AUDIT_USER_SELF_UPDATE = "identity.user.self_update"
AUDIT_USER_SELF_CHANGE_PASSWORD = "identity.user.self_change_password"

#: 事件编码 — SPEC 5.7: ``<MODULE>.<EVENT>``。
#: auth 模块（TASK-013）注册事务内处理器监听这些事件，
#: 在当前事务内吊销用户会话（SPEC 12.3）。
EVENT_USER_DISABLED = "USER.DISABLED"
EVENT_PASSWORD_RESET_BY_ADMIN = "USER.PASSWORD_RESET_BY_ADMIN"

#: Alembic 迁移版本目录（相对于项目根 / CWD）。
ALEMBIC_VERSION_DIR = "src/app/modules/identity/migrations"


# ── ModuleDefinition 实例 ──────────────────────────────────────────────────

MODULE_DEFINITION = ModuleDefinition(
    code=MODULE_CODE,
    api_tag=MODULE_API_TAG,
    # SPEC 5.5: 模块公开的 Application Port。
    # identity 模块的 UserRepository Port 在模块内部使用，
    # 未作为跨模块公开 Port 声明。后续跨模块协作（如 auth 模块
    # 查询用户状态）通过事件解耦——本模块发布事件，auth 模块注册处理器。
    application_ports=(),
    required_dependencies=("audit",),  # 审计写入依赖 audit 模块
    optional_dependencies=(),
    routers=(identity_router,),
    permission_codes=(
        PERMISSION_USER_READ,
        PERMISSION_USER_WRITE,
    ),
    error_codes=(
        USER_NOT_FOUND,
        USER_ALREADY_EXISTS,
        USER_ALREADY_DISABLED,
        USER_ALREADY_ACTIVE,
        USER_INVALID_OLD_PASSWORD,
        USER_HAS_AUDIT_RECORDS,
    ),
    audit_actions=(
        AUDIT_USER_CREATE,
        AUDIT_USER_UPDATE,
        AUDIT_USER_ENABLE,
        AUDIT_USER_DISABLE,
        AUDIT_USER_RESET_PASSWORD,
        AUDIT_USER_SELF_UPDATE,
        AUDIT_USER_SELF_CHANGE_PASSWORD,
    ),
    protected_resource_types=("user",),
    initializers=(),  # 首个管理员由 auth 模块（TASK-013）初始化器创建。
    management_commands=(),  # 身份命令由 TASK-013 实现。
    event_handlers=(),  # 处理器由 auth 模块（TASK-013）注册。
    event_codes=(
        EVENT_USER_DISABLED,
        EVENT_PASSWORD_RESET_BY_ADMIN,
    ),
    alembic_version_dir=ALEMBIC_VERSION_DIR,
)
