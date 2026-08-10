"""模块校验异常 — SPEC 5.5.

组合根启动校验失败时抛出这些异常。每类异常指明冲突来源，
满足 SPEC 5.5: "应用启动与 CI 必须失败并指出冲突来源"。
"""

from __future__ import annotations


class ModuleValidationError(Exception):
    """模块校验失败基类 — SPEC 5.5.

    所有模块校验异常的基类。校验失败时应用启动和 CI 必须失败
    （SPEC 5.5: "应用启动与 CI 必须失败并指出冲突来源"）。
    """


class DuplicateDeclarationError(ModuleValidationError):
    """重复声明冲突 — 同一项被多个模块声明.

    SPEC 5.5: "Router、权限点、错误码、审计动作、资源类型和命令
    发生重复时，应用启动与 CI 必须失败并指出冲突来源"。
    """


class MissingDependencyError(ModuleValidationError):
    """必需依赖未启用 — 模块声明的必需依赖不在模块清单中.

    SPEC 5.5: "必需依赖未启用时，应用启动与 CI 必须失败
    并指出冲突来源"。
    """


class CircularDependencyError(ModuleValidationError):
    """循环依赖 — 模块依赖图构成环.

    SPEC 5.5: "依赖构成循环时，应用启动与 CI 必须失败
    并指出冲突来源"。
    """


class OptionalDependencyNotClosedError(ModuleValidationError):
    """可选依赖能力未按声明关闭 — SPEC 5.5.

    SPEC 5.5: "可选依赖对应的能力在依赖未启用时必须整体关闭"。
    当模块声明了可选依赖，但依赖关系无法满足声明约束时抛出。
    """


class InvalidModuleCodeFormatError(ModuleValidationError):
    """模块编码格式非法 — SPEC 5.5.

    模块编码必须为小写字母、数字和下划线，以字母开头。
    """


class InvalidPermissionCodeFormatError(ModuleValidationError):
    """权限编码格式非法 — SPEC 5.5.

    SPEC 5.5: "权限编码固定为小写三段或多段形式，例如 system:user:read"。
    """
