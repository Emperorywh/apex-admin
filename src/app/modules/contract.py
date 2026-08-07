"""模块接入契约（SPEC §5.5）。

每个业务模块在模块根目录公开唯一的 :class:`ModuleDefinition`，
由 Composition Root 中的显式模块清单装配。
禁止通过扫描包、导入副作用或命名约定自动发现模块（SPEC §5.5、§32）。

``ModuleDefinition`` 只声明以下公开信息（SPEC §5.5）：

- 全局唯一且稳定的模块编码。
- 模块公开的 Application Port。
- 必需依赖和可选依赖的其他模块编码。
- Router 列表和 API Tag。
- 权限点、错误码、审计动作和受保护资源类型。
- 幂等初始化器和管理命令。
- 事务内事件处理器和可选集成事件处理器。
- Alembic 迁移版本目录。

本模块只定义数据结构，不包含任何运行时副作用。
所有声明类型使用 ``frozen=True`` dataclass，确保模块声明构造后不可变。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter

from app.ports.unit_of_work import UnitOfWork

# ---------------------------------------------------------------------------
# 稳定编码值类型
#
# 以下类型是模块声明中使用的值对象，每个编码全局唯一且稳定。
# SPEC §5.5：权限编码固定为小写三段或多段形式，例如 ``system:user:read``。
# SPEC §5.5：业务错误码固定为 ``<MODULE>.<REASON>``，只允许大写字母、数字和下划线。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionPoint:
    """权限点声明（SPEC §5.5、§13.1）。

    权限编码表达资源和操作，固定为小写三段或多段形式，
    例如 ``system:user:read``（SPEC §5.5）。
    权限点全局唯一，重复时应用启动与 CI 必须失败。

    Attributes:
        code: 权限编码，例如 ``system:user:read``
        description: 权限用途说明
    """

    code: str
    description: str


@dataclass(frozen=True)
class ErrorCode:
    """错误码声明（SPEC §5.5、§10.2）。

    业务错误码固定为 ``<MODULE>.<REASON>`` 格式，只允许大写字母、数字和下划线，
    例如 ``USER.NOT_FOUND``（SPEC §5.5）。错误码全局唯一且稳定，
    客户端业务判断只能使用错误码，不得依赖展示文案。

    Attributes:
        code: 错误码，格式 ``<MODULE>.<REASON>``
        http_status: 对应的 HTTP 状态码
        description: 错误含义和适用场景说明
    """

    code: str
    http_status: int
    description: str


@dataclass(frozen=True)
class AuditAction:
    """审计动作声明（SPEC §5.5、§18.2）。

    操作审计记录操作模块和动作，动作编码全局唯一且稳定。
    审计日志不通过普通业务 CRUD 修改（SPEC §18.2）。

    Attributes:
        code: 审计动作编码
        description: 动作用途说明
    """

    code: str
    description: str


@dataclass(frozen=True)
class ResourceType:
    """受保护资源类型声明（SPEC §5.5）。

    每个模块声明其管理的资源类型，用于权限校验和审计目标标识。
    资源类型编码全局唯一。

    Attributes:
        code: 资源类型编码
        description: 资源类型说明
    """

    code: str
    description: str


@dataclass(frozen=True)
class EventDefinition:
    """事件声明（SPEC §5.7）。

    Domain Event 是不依赖 FastAPI、ORM 和基础设施的不可变对象。
    跨模块事件载荷只允许稳定编码、标量值和资源 ID（SPEC §5.7）。
    事件编码全局唯一，重复时应用启动与 CI 必须失败。

    Attributes:
        code: 事件编码
        description: 事件用途说明
    """

    code: str
    description: str


@dataclass(frozen=True)
class EventHandlerDefinition:
    """事件处理器声明（SPEC §5.7）。

    事务内事件处理器（``transactional=True``）在当前 Unit of Work 提交前
    同步执行，任一失败时整个 Use Case 回滚（SPEC §5.7）。
    事务内处理器不得执行邮件、Webhook、远程 HTTP 调用等不可回滚副作用。

    可选集成事件处理器（``transactional=False``）用于事务后副作用，
    仅在持久化任务扩展启用时注册。

    重复处理器编码必须使启动和 CI 失败（SPEC §5.7）。
    执行逻辑由 TASK-010 实现。

    Attributes:
        code: 处理器编码，全局唯一
        event_code: 所处理事件的编码
        description: 处理器用途说明
        transactional: 是否为事务内处理器，默认 True
    """

    code: str
    event_code: str
    description: str
    transactional: bool = True


@dataclass(frozen=True)
class CommandDefinition:
    """管理命令声明（SPEC §5.5、§25.1）。

    管理命令在模块中声明，命令编码全局唯一。
    重复命令编码时应用启动与 CI 必须失败。
    CLI 实现由 TASK-012 实现。

    Attributes:
        code: 命令编码
        description: 命令用途说明
    """

    code: str
    description: str


# ---------------------------------------------------------------------------
# 幂等初始化器（SPEC §8.5）
# ---------------------------------------------------------------------------

# 幂等初始化器函数类型：接收 Unit of Work，执行幂等 upsert
InitializerFn = Callable[[UnitOfWork], Awaitable[None]]


@dataclass(frozen=True)
class Initializer:
    """幂等初始化器声明（SPEC §8.5）。

    初始化器使用稳定自然键或稳定编码执行幂等 upsert，
    不得按显示名称判断重复（SPEC §8.5）。
    初始化过程可重复执行且不会创建重复数据。
    每个初始化器只能写入本模块拥有的数据。

    Attributes:
        code: 初始化器编码，在模块内唯一
        description: 用途说明
        run: 异步初始化函数，接收 Unit of Work 并执行幂等 upsert
    """

    code: str
    description: str
    run: InitializerFn


# ---------------------------------------------------------------------------
# 模块定义（SPEC §5.5）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleDefinition:
    """模块定义（SPEC §5.5）。

    每个业务模块公开唯一的 ``ModuleDefinition`` 实例，
    由 Composition Root 的显式模块清单装配（无扫描、无导入副作用）。

    所有字段在构造后不可变。frozen dataclass 确保模块声明的稳定性。

    新增模块只允许新增模块自身代码，并在 Composition Root 的模块清单中
    增加一项；不得修改核心模块内部实现（SPEC §5.5）。

    Attributes:
        code: 全局唯一且稳定的模块编码
        name: 人类可读的模块名称
        description: 模块用途说明
        application_port: 模块公开的 Application Port 类（其他模块依赖此接口）
        required_dependencies: 必需依赖的模块编码集合，未启用时启动失败
        optional_dependencies: 可选依赖的模块编码集合，未启用时其能力整体关闭
        routers: FastAPI Router 列表
        api_tag: OpenAPI 文档分组标签
        permission_points: 模块声明的权限点集合
        error_codes: 模块声明的错误码集合
        audit_actions: 模块声明的审计动作集合
        resource_types: 模块声明的受保护资源类型集合
        initializers: 模块注册的幂等初始化器列表
        events: 模块声明的事件集合
        event_handlers: 模块注册的事件处理器集合
        commands: 模块声明的管理命令集合
        migration_version_dir: Alembic 迁移版本目录路径
    """

    code: str
    name: str
    description: str
    application_port: type
    api_tag: str
    required_dependencies: frozenset[str] = field(default_factory=frozenset)
    optional_dependencies: frozenset[str] = field(default_factory=frozenset)
    routers: tuple[APIRouter, ...] = field(default_factory=tuple)
    permission_points: frozenset[PermissionPoint] = field(default_factory=frozenset)
    error_codes: frozenset[ErrorCode] = field(default_factory=frozenset)
    audit_actions: frozenset[AuditAction] = field(default_factory=frozenset)
    resource_types: frozenset[ResourceType] = field(default_factory=frozenset)
    initializers: tuple[Initializer, ...] = field(default_factory=tuple)
    events: frozenset[EventDefinition] = field(default_factory=frozenset)
    event_handlers: frozenset[EventHandlerDefinition] = field(default_factory=frozenset)
    commands: frozenset[CommandDefinition] = field(default_factory=frozenset)
    migration_version_dir: Path | None = None
