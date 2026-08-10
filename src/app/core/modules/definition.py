"""模块接入契约 ModuleDefinition — SPEC 5.5.

SPEC 5.5: 每个业务模块必须在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配。禁止通过扫描包、导入副作用
或命名约定自动发现模块。

``ModuleDefinition`` 只声明以下公开信息（SPEC 5.5）:
  - 全局唯一且稳定的模块编码。
  - 模块公开的 Application Port。
  - 必需依赖和可选依赖的其他模块编码。
  - Router 列表和 API Tag。
  - 权限点、错误码、审计动作和受保护资源类型。
  - 幂等初始化器和管理命令。
  - 事务内事件处理器和事件编码。
  - Alembic 迁移版本目录。

命名格式规范（SPEC 5.5）:
  - 模块编码: 小写字母、数字和下划线，如 ``user``、``auth``。
  - 权限编码: 小写三段或多段，如 ``system:user:read``。
  - 错误码: ``<MODULE>.<REASON>``，仅大写字母、数字和下划线。
  - 事件编码: ``<MODULE>.<EVENT>``，仅大写字母、数字和下划线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter

    from app.core.events.handlers import TransactionalEventHandler
    from app.core.initialization.framework import Initializer


# 模块编码格式：小写字母开头，可含小写字母、数字和下划线。
_MODULE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# 权限编码格式：小写三段或多段，以冒号分隔（SPEC 5.5）。
_PERMISSION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*){2,}$")

# 管理命令名格式：小写字母、数字、连字符和空格。
_COMMAND_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9\- ]*$")


def validate_module_code(code: str) -> None:
    """校验模块编码格式.

    SPEC 5.5: 模块编码必须为小写字母、数字和下划线，以字母开头。

    参数:
        code: 待校验的模块编码。

    抛出:
        ValueError: 编码格式不合法。
    """

    if not isinstance(code, str) or not _MODULE_CODE_PATTERN.match(code):
        raise ValueError(
            f"模块编码格式非法: {code!r}，应为小写字母、数字和下划线，以字母开头",
        )


def validate_permission_code(code: str) -> None:
    """校验权限编码格式.

    SPEC 5.5: "权限编码固定为小写三段或多段形式，例如 system:user:read"。

    参数:
        code: 待校验的权限编码。

    抛出:
        ValueError: 编码格式不合法。
    """

    if not isinstance(code, str) or not _PERMISSION_CODE_PATTERN.match(code):
        raise ValueError(
            f"权限编码格式非法: {code!r}，"
            f"应为小写三段或多段形式（如 system:user:read）",
        )


@dataclass(frozen=True)
class ManagementCommand:
    """管理命令声明 — SPEC 25.1.

    每个模块通过 ``ModuleDefinition`` 声明其提供的管理命令。
    命令名称全局唯一（SPEC 5.5: "命令发生重复时，应用启动与 CI 必须失败"）。

    属性:
        name: 命令名称（如 ``"auth sync-permissions"``）。
        description: 命令用途说明。
    """

    name: str
    description: str


@dataclass(frozen=True)
class ModuleDefinition:
    """模块接入契约 — SPEC 5.5.

    每个业务模块在模块根目录公开唯一的 ``ModuleDefinition`` 实例。
    Composition Root 的显式模块清单引用这些实例，在应用启动时
    进行全量校验（SPEC 5.5）。

    所有集合字段为 tuple（不可变），确保 ``ModuleDefinition`` 实例
    创建后不被修改。

    属性:
        code: 全局唯一且稳定的模块编码（小写字母、数字和下划线）。
        api_tag: API Tag，用于 OpenAPI 分组，全局唯一。
        application_ports: 模块公开的 Application Port 类型列表。
        required_dependencies: 必需依赖的模块编码列表。
        optional_dependencies: 可选依赖的模块编码列表。
        routers: Router 列表（FastAPI APIRouter 实例）。
        permission_codes: 权限编码列表（小写多段，如 ``system:user:read``）。
        error_codes: 错误码列表（``<MODULE>.<REASON>``）。
        audit_actions: 审计动作编码列表。
        protected_resource_types: 受保护资源类型列表。
        initializers: 幂等初始化器列表。
        management_commands: 管理命令声明列表。
        event_handlers: 事务内事件处理器列表。
        event_codes: 模块产生的事件编码列表。
        alembic_version_dir: Alembic 迁移版本目录路径（相对于项目根），
                             无迁移时为 None。
    """

    code: str
    api_tag: str
    application_ports: tuple[type, ...] = ()
    required_dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    routers: tuple[APIRouter, ...] = field(default_factory=tuple)
    permission_codes: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    audit_actions: tuple[str, ...] = ()
    protected_resource_types: tuple[str, ...] = ()
    initializers: tuple[Initializer, ...] = field(default_factory=tuple)
    management_commands: tuple[ManagementCommand, ...] = ()
    event_handlers: tuple[TransactionalEventHandler, ...] = field(default_factory=tuple)
    event_codes: tuple[str, ...] = ()
    alembic_version_dir: str | None = None

    def __post_init__(self) -> None:
        """构造后校验编码格式.

        SPEC 5.5: 模块编码和权限编码具有格式约束。
        在构造时校验，确保格式非法的 ModuleDefinition 无法创建。
        """

        validate_module_code(self.code)
        for perm in self.permission_codes:
            validate_permission_code(perm)

    def to_summary(self) -> dict[str, Any]:
        """返回模块摘要信息（用于校验报告）.

        返回模块的公开声明信息，不包含 Router 实例和处理器实例等
        不可序列化对象。用于 ``modules validate`` 命令输出。
        """

        return {
            "code": self.code,
            "api_tag": self.api_tag,
            "required_dependencies": list(self.required_dependencies),
            "optional_dependencies": list(self.optional_dependencies),
            "permission_codes": list(self.permission_codes),
            "error_codes": list(self.error_codes),
            "audit_actions": list(self.audit_actions),
            "protected_resource_types": list(self.protected_resource_types),
            "management_commands": [c.name for c in self.management_commands],
            "event_codes": list(self.event_codes),
            "event_handler_codes": [h.code for h in self.event_handlers],
            "initializer_codes": [i.code for i in self.initializers],
            "alembic_version_dir": self.alembic_version_dir,
        }
