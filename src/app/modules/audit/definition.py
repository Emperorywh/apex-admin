"""审计模块定义（SPEC §5.5、§18.1–18.2）。

公开唯一的 :class:`~app.modules.contract.ModuleDefinition` 实例，
由 Composition Root 的显式模块清单装配。

模块声明全部公开信息：模块编码、Application Port、Router（G2 阶段无路由——
查询 API 在 TASK-026 实现）、权限点、错误码、资源类型和迁移版本目录。
"""

from __future__ import annotations

from pathlib import Path

from app.modules.audit.application.port import AuditApplicationPort as _Port
from app.modules.contract import (
    ErrorCode,
    ModuleDefinition,
    ResourceType,
)

#: 审计模块迁移版本目录（与全局 Alembic ``versions/`` 目录一致，SPEC §8.2）
_MIGRATION_VERSION_DIR = Path(__file__).resolve().parents[3] / (
    "infrastructure/database/migrations/versions"
)

MODULE: ModuleDefinition = ModuleDefinition(
    code="audit",
    name="审计与安全日志模块",
    description=(
        "操作审计模型、登录日志模型、Use Case 审计 Port、事务内审计记录、"
        "差异中敏感字段过滤、显示名称快照和安全事件日志（SPEC §18.1–18.2、§5.7）。"
        "查询 API 和日志保留在 TASK-026 实现。"
    ),
    application_port=_Port,
    api_tag="audit",
    routers=(),
    error_codes=frozenset(
        {
            ErrorCode(
                code="AUDIT.RECORD_NOT_FOUND",
                http_status=404,
                description="审计记录不存在",
            ),
        }
    ),
    resource_types=frozenset(
        {
            ResourceType(
                code="audit:record",
                description="操作审计记录资源",
            ),
            ResourceType(
                code="audit:login_log",
                description="登录日志资源",
            ),
        }
    ),
    migration_version_dir=_MIGRATION_VERSION_DIR,
)
