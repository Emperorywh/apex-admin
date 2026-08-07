"""模块接入契约与注册框架（SPEC §5.5）。

本包定义模块声明契约 (:class:`~app.modules.contract.ModuleDefinition`)、
注册校验 (:class:`~app.modules.registry.ModuleRegistry`) 和
幂等初始化框架 (:class:`~app.modules.initialization.InitializationRunner`)。

Composition Root 通过 :mod:`~app.composition_root` 提供显式模块清单，
ModuleRegistry 在启动时校验全部声明。
"""

from app.modules.contract import (
    AuditAction,
    CommandDefinition,
    ErrorCode,
    EventDefinition,
    EventHandlerDefinition,
    Initializer,
    InitializerFn,
    ModuleDefinition,
    PermissionPoint,
    ResourceType,
)
from app.modules.initialization import InitializationRunner
from app.modules.registry import ModuleRegistrationError, ModuleRegistry, RegisteredModule

__all__ = [
    "AuditAction",
    "CommandDefinition",
    "ErrorCode",
    "EventDefinition",
    "EventHandlerDefinition",
    "InitializationRunner",
    "Initializer",
    "InitializerFn",
    "ModuleDefinition",
    "ModuleRegistrationError",
    "ModuleRegistry",
    "PermissionPoint",
    "RegisteredModule",
    "ResourceType",
]
