"""模块接入契约与组合根校验 — SPEC 5.5.

每个业务模块在模块根目录公开唯一的 ``ModuleDefinition``，
由 Composition Root 中的显式模块清单装配。禁止通过扫描包、
导入副作用或命名约定自动发现模块
（SPEC 5.5 / SPEC 32: "不通过包扫描、导入副作用或全局 Service
Locator 自动注册模块"）。

SPEC 5.5 注册规则:
  - 新增模块只允许新增模块自身代码，并在 Composition Root 的模块清单中增加一项。
  - Router、权限点、错误码、审计动作、资源类型和命令发生重复时，
    应用启动与 CI 必须失败并指出冲突来源。
  - 模块依赖只允许指向其他模块的公开 Application Port；
    必需依赖未启用、依赖构成循环或可选依赖能力未按声明关闭时，
    应用启动与 CI 必须失败并指出冲突来源。
  - 权限编码固定为小写三段或多段形式（如 ``system:user:read``）。
  - 业务错误码固定为 ``<MODULE>.<REASON>``（如 ``USER.NOT_FOUND``）。
  - 每个模块拥有自己的表、ORM 模型和 Repository Adapter。

公开 API:
  - ``ModuleDefinition``: 模块接入契约
  - ``ManagementCommand``: 管理命令声明
  - ``ModuleRegistry``: 模块注册表与启动校验
  - ``ValidationResult``: 校验结果
"""
