"""认证模块定义（SPEC §5.5、§12）。

公开唯一的 :class:`~app.modules.contract.ModuleDefinition` 实例，
由 Composition Root 的显式模块清单装配。

模块声明全部公开信息：模块编码、Application Port、Router、权限点、
错误码、资源类型、事件、事件处理器和迁移版本目录。

认证模块依赖用户模块（``required_dependencies={"user"}``），
在同一事务中查询用户、校验密码并升级哈希（SPEC §5.6、§12.1）。
"""

from __future__ import annotations

from pathlib import Path

from app.modules.auth.application.port import AuthApplicationPort as _Port
from app.modules.auth.routes import auth_router
from app.modules.contract import (
    ErrorCode,
    EventDefinition,
    EventHandlerDefinition,
    ModuleDefinition,
    PermissionPoint,
    ResourceType,
)

#: 认证模块迁移版本目录（与全局 Alembic ``versions/`` 目录一致，SPEC §8.2）
_MIGRATION_VERSION_DIR = Path(__file__).resolve().parents[3] / (
    "infrastructure/database/migrations/versions"
)

MODULE: ModuleDefinition = ModuleDefinition(
    code="auth",
    name="认证与会话模块",
    description=(
        "账号密码认证、服务端会话管理、Token 生成与存储（SPEC §12）。"
        "实现登录/登出端点、Argon2id 验证（含 check_needs_rehash）、"
        "会话持久化、Access/Refresh Token 生成与 HMAC-SHA-256 摘要存储。"
    ),
    application_port=_Port,
    api_tag="auth",
    routers=(auth_router,),
    required_dependencies=frozenset({"user"}),
    permission_points=frozenset(
        {
            PermissionPoint(
                code="system:auth:login",
                description="登录",
            ),
            PermissionPoint(
                code="system:auth:logout",
                description="登出",
            ),
        }
    ),
    error_codes=frozenset(
        {
            ErrorCode(
                code="AUTH.INVALID_CREDENTIALS",
                http_status=401,
                description="用户名或密码不正确",
            ),
        }
    ),
    resource_types=frozenset(
        {
            ResourceType(
                code="auth:session",
                description="认证会话资源",
            ),
        }
    ),
    events=frozenset(
        {
            EventDefinition(
                code="auth.session.created",
                description="会话创建（登录成功）事件",
            ),
            EventDefinition(
                code="auth.session.revoked",
                description="会话吊销（退出登录）事件",
            ),
        }
    ),
    event_handlers=frozenset(
        {
            EventHandlerDefinition(
                code="auth.handler.session_created",
                event_code="auth.session.created",
                description="记录会话创建事件（事务内处理器）",
                transactional=True,
            ),
            EventHandlerDefinition(
                code="auth.handler.session_revoked",
                event_code="auth.session.revoked",
                description="记录会话吊销事件（事务内处理器）",
                transactional=True,
            ),
        }
    ),
    migration_version_dir=_MIGRATION_VERSION_DIR,
)
