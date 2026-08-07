"""RBAC 模块定义（SPEC §5.5、§13）。

公开唯一的 :class:`~app.modules.contract.ModuleDefinition` 实例，
由 Composition Root 的显式模块清单装配。

模块声明全部公开信息：模块编码、Application Port、Router、权限点、
错误码、审计动作、资源类型、事件、事件处理器和迁移版本目录。

RBAC 模块依赖用户模块和认证模块（``required_dependencies={"user", "auth"}``），
在同一事务中查询用户、验证 Token 并加载权限关系。
"""

from __future__ import annotations

from pathlib import Path

from app.modules.contract import (
    AuditAction,
    ErrorCode,
    EventDefinition,
    EventHandlerDefinition,
    ModuleDefinition,
    PermissionPoint,
    ResourceType,
)
from app.modules.rbac.application.port import RbacApplicationPort as _Port
from app.modules.rbac.routes import roles_router, user_role_router

#: RBAC 模块迁移版本目录（与全局 Alembic ``versions/`` 目录一致，SPEC §8.2）
_MIGRATION_VERSION_DIR = Path(__file__).resolve().parents[3] / (
    "infrastructure/database/migrations/versions"
)

MODULE: ModuleDefinition = ModuleDefinition(
    code="rbac",
    name="RBAC 与授权模块",
    description=(
        "角色模型、权限点模型、用户-角色关系、角色-权限关系、"
        "角色管理 API、权限分配 API、统一认证/授权依赖、"
        "管理范围强制和超级管理员保护（SPEC §13.1–13.4、§23.5）。"
    ),
    application_port=_Port,
    api_tag="rbac",
    routers=(roles_router, user_role_router),
    required_dependencies=frozenset({"user", "auth"}),
    permission_points=frozenset(
        {
            # 角色管理权限点
            PermissionPoint(
                code="system:role:create",
                description="创建角色",
            ),
            PermissionPoint(
                code="system:role:read",
                description="查询角色",
            ),
            PermissionPoint(
                code="system:role:update",
                description="更新角色",
            ),
            PermissionPoint(
                code="system:role:enable",
                description="启用角色",
            ),
            PermissionPoint(
                code="system:role:disable",
                description="禁用角色",
            ),
            PermissionPoint(
                code="system:role:assign_permissions",
                description="为角色分配权限",
            ),
            PermissionPoint(
                code="system:role:read_members",
                description="查询角色成员",
            ),
            # 用户-角色分配权限点
            PermissionPoint(
                code="system:user:assign_roles",
                description="为用户分配角色",
            ),
        }
    ),
    error_codes=frozenset(
        {
            ErrorCode(
                code="RBAC.ROLE_NOT_FOUND",
                http_status=404,
                description="角色不存在",
            ),
            ErrorCode(
                code="RBAC.ROLE_ALREADY_EXISTS",
                http_status=409,
                description="角色编码已存在",
            ),
            ErrorCode(
                code="RBAC.INSUFFICIENT_SCOPE",
                http_status=403,
                description="管理范围不足",
            ),
            ErrorCode(
                code="RBAC.BUILTIN_ROLE_PROTECTED",
                http_status=409,
                description="内置角色受保护",
            ),
            ErrorCode(
                code="RBAC.LAST_SUPER_ADMIN",
                http_status=409,
                description="不能移除系统最后一个可用超级管理员",
            ),
            ErrorCode(
                code="RBAC.FORBIDDEN",
                http_status=403,
                description="无权限执行此操作",
            ),
        }
    ),
    audit_actions=frozenset(
        {
            AuditAction(code="role.create", description="创建角色"),
            AuditAction(code="role.update", description="更新角色"),
            AuditAction(code="role.enable", description="启用角色"),
            AuditAction(code="role.disable", description="禁用角色"),
            AuditAction(code="role.assign_permission", description="为角色分配权限"),
            AuditAction(code="user_role.assign", description="为用户分配角色"),
            AuditAction(code="user_role.remove", description="移除用户角色"),
        }
    ),
    resource_types=frozenset(
        {
            ResourceType(
                code="rbac:role",
                description="角色资源",
            ),
        }
    ),
    events=frozenset(
        {
            EventDefinition(
                code="rbac.role.created",
                description="角色创建事件",
            ),
            EventDefinition(
                code="rbac.role.disabled",
                description="角色禁用事件",
            ),
            EventDefinition(
                code="rbac.user_role.assigned",
                description="用户角色分配事件",
            ),
            EventDefinition(
                code="rbac.user_role.removed",
                description="用户角色移除事件",
            ),
        }
    ),
    event_handlers=frozenset(
        {
            EventHandlerDefinition(
                code="rbac.handler.role_created",
                event_code="rbac.role.created",
                description="记录角色创建事件（事务内处理器）",
                transactional=True,
            ),
            EventHandlerDefinition(
                code="rbac.handler.role_disabled",
                event_code="rbac.role.disabled",
                description="记录角色禁用事件（事务内处理器）",
                transactional=True,
            ),
            EventHandlerDefinition(
                code="rbac.handler.user_role_assigned",
                event_code="rbac.user_role.assigned",
                description="记录用户角色分配事件（事务内处理器）",
                transactional=True,
            ),
            EventHandlerDefinition(
                code="rbac.handler.user_role_removed",
                event_code="rbac.user_role.removed",
                description="记录用户角色移除事件（事务内处理器）",
                transactional=True,
            ),
        }
    ),
    migration_version_dir=_MIGRATION_VERSION_DIR,
)
