"""示例模块定义（SPEC §5.5）。

公开唯一的 :class:`~app.modules.contract.ModuleDefinition` 实例，
由 Composition Root 的显式模块清单装配。

模块声明全部公开信息：模块编码、Application Port、Router、权限点、
错误码、审计动作、资源类型、事件、事件处理器和迁移版本目录。
"""

from __future__ import annotations

from pathlib import Path

from app.modules.contract import (
    CommandDefinition,
    ErrorCode,
    EventDefinition,
    EventHandlerDefinition,
    ModuleDefinition,
    PermissionPoint,
    ResourceType,
)
from app.modules.example.application.port import ExampleApplicationPort as _Port
from app.modules.example.routes import router

#: 示例模块迁移版本目录（与全局 Alembic ``versions/`` 目录一致，SPEC §8.2）
_MIGRATION_VERSION_DIR = Path(__file__).resolve().parents[3] / (
    "infrastructure/database/migrations/versions"
)

MODULE: ModuleDefinition = ModuleDefinition(
    code="example",
    name="示例模块",
    description=(
        "最小示例模块，验证 Router、Use Case、Port、Adapter、迁移、"
        "权限码、错误码和事件的完整接入模式（SPEC §30.2）。"
        "不携带业务演示数据。"
    ),
    application_port=_Port,
    api_tag="examples",
    routers=(router,),
    permission_points=frozenset(
        {
            PermissionPoint(
                code="example:item:create",
                description="创建示例项目",
            ),
            PermissionPoint(
                code="example:item:read",
                description="查询示例项目",
            ),
        }
    ),
    error_codes=frozenset(
        {
            ErrorCode(
                code="EXAMPLE.NOT_FOUND",
                http_status=404,
                description="示例项目不存在",
            ),
            ErrorCode(
                code="EXAMPLE.INVALID_NAME",
                http_status=400,
                description="示例项目名称不合规",
            ),
        }
    ),
    resource_types=frozenset(
        {
            ResourceType(
                code="example:item",
                description="示例项目资源",
            ),
        }
    ),
    events=frozenset(
        {
            EventDefinition(
                code="example.item.created",
                description="示例项目创建事件",
            ),
        }
    ),
    event_handlers=frozenset(
        {
            EventHandlerDefinition(
                code="example.handler.item_created",
                event_code="example.item.created",
                description="记录示例项目创建事件（事务内处理器）",
                transactional=True,
            ),
        }
    ),
    commands=frozenset(
        {
            CommandDefinition(
                code="example.noop",
                description="示例模块占位命令（不执行业务操作）",
            ),
        }
    ),
    migration_version_dir=_MIGRATION_VERSION_DIR,
)
