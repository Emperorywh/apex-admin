"""用户模块定义（SPEC §5.5、§11）。

公开唯一的 :class:`~app.modules.contract.ModuleDefinition` 实例，
由 Composition Root 的显式模块清单装配。

模块声明全部公开信息：模块编码、Application Port、Router、权限点、
错误码、审计动作、资源类型、事件、事件处理器和迁移版本目录。

权限点覆盖用户管理的全部管理操作和自助操作（SPEC §23.5：
所有管理接口具有权限点）。
"""

from __future__ import annotations

from pathlib import Path

from app.modules.contract import (
    ErrorCode,
    EventDefinition,
    EventHandlerDefinition,
    ModuleDefinition,
    PermissionPoint,
    ResourceType,
)
from app.modules.user.application.port import UserApplicationPort as _Port
from app.modules.user.routes import admin_router, self_router

#: 用户模块迁移版本目录（与全局 Alembic ``versions/`` 目录一致，SPEC §8.2）
_MIGRATION_VERSION_DIR = Path(__file__).resolve().parents[3] / (
    "infrastructure/database/migrations/versions"
)

MODULE: ModuleDefinition = ModuleDefinition(
    code="user",
    name="用户管理模块",
    description=(
        "用户实体、密码哈希、CRUD API、启用/禁用、重置密码、"
        "自助改密和自助资料读写（SPEC §11）。"
        "Argon2id 参数在此模块定义，被认证模块复用。"
    ),
    application_port=_Port,
    api_tag="users",
    routers=(admin_router, self_router),
    permission_points=frozenset(
        {
            # 管理操作权限点（SPEC §23.5）
            PermissionPoint(
                code="system:user:create",
                description="创建用户",
            ),
            PermissionPoint(
                code="system:user:read",
                description="查询用户",
            ),
            PermissionPoint(
                code="system:user:update",
                description="更新用户资料",
            ),
            PermissionPoint(
                code="system:user:enable",
                description="启用用户",
            ),
            PermissionPoint(
                code="system:user:disable",
                description="禁用用户",
            ),
            PermissionPoint(
                code="system:user:reset_password",
                description="重置用户密码",
            ),
            # 自助操作权限点（SPEC §23.5）
            PermissionPoint(
                code="system:user:self_read",
                description="查询自身资料",
            ),
            PermissionPoint(
                code="system:user:self_update",
                description="更新自身资料",
            ),
            PermissionPoint(
                code="system:user:self_password",
                description="修改自身密码",
            ),
        }
    ),
    error_codes=frozenset(
        {
            ErrorCode(
                code="USER.NOT_FOUND",
                http_status=404,
                description="用户不存在",
            ),
            ErrorCode(
                code="USER.ALREADY_EXISTS",
                http_status=409,
                description="用户名已存在",
            ),
            ErrorCode(
                code="USER.INVALID_INPUT",
                http_status=400,
                description="用户名或密码不符合规则",
            ),
            ErrorCode(
                code="USER.INVALID_PASSWORD",
                http_status=400,
                description="新密码不符合规则",
            ),
            ErrorCode(
                code="USER.INVALID_CREDENTIALS",
                http_status=400,
                description="当前密码不正确",
            ),
            ErrorCode(
                code="USER.LAST_SUPER_ADMIN",
                http_status=409,
                description="不能禁用或删除系统最后一个可用超级管理员",
            ),
        }
    ),
    resource_types=frozenset(
        {
            ResourceType(
                code="user:account",
                description="用户账号资源",
            ),
        }
    ),
    events=frozenset(
        {
            EventDefinition(
                code="user.created",
                description="用户创建事件",
            ),
            EventDefinition(
                code="user.disabled",
                description="用户禁用事件",
            ),
        }
    ),
    event_handlers=frozenset(
        {
            EventHandlerDefinition(
                code="user.handler.created",
                event_code="user.created",
                description="记录用户创建事件（事务内处理器）",
                transactional=True,
            ),
            EventHandlerDefinition(
                code="user.handler.disabled",
                event_code="user.disabled",
                description="记录用户禁用事件（事务内处理器）",
                transactional=True,
            ),
        }
    ),
    migration_version_dir=_MIGRATION_VERSION_DIR,
)
