"""模块注册表与启动校验 — SPEC 5.5.

SPEC 5.5 注册规则:
  - Router、权限点、错误码、审计动作、资源类型和命令发生重复时，
    应用启动与 CI 必须失败并指出冲突来源。
  - 必需依赖未启用、依赖构成循环或可选依赖能力未按声明关闭时，
    应用启动与 CI 必须失败并指出冲突来源。

``ModuleRegistry`` 收集 Composition Root 显式声明的 ``ModuleDefinition``
列表，在应用启动时执行全量校验。校验通过后提供已注册模块的查询能力。

校验内容:
  1. 模块编码全局唯一。
  2. API Tag 全局唯一（标识模块的 Router/API 表面）。
  3. 权限编码全局唯一。
  4. 错误码全局唯一。
  5. 审计动作编码全局唯一。
  6. 受保护资源类型全局唯一。
  7. 管理命令名称全局唯一。
  8. 事件编码全局唯一。
  9. 处理器编码全局唯一。
  10. 必需依赖必须存在于模块清单中。
  11. 依赖图无循环（含必需和可选依赖）。
  12. 可选依赖的声明一致性。

校验失败时抛出对应的 ``ModuleValidationError`` 子类，
错误消息指明冲突来源（SPEC 5.5: "指出冲突来源"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.core.modules.exceptions import (
    CircularDependencyError,
    DuplicateDeclarationError,
    MissingDependencyError,
    OptionalDependencyNotClosedError,
)

if TYPE_CHECKING:
    from app.core.modules.definition import ModuleDefinition


@dataclass(frozen=True)
class ConflictRecord:
    """重复声明冲突记录 — 指明冲突来源（SPEC 5.5）.

    属性:
        category: 冲突类别（如 ``"module_code"``、``"permission_code"``）。
        value: 发生冲突的值。
        modules: 声明了此值的模块编码列表。
    """

    category: str
    value: str
    modules: tuple[str, ...]


@dataclass
class ValidationResult:
    """模块校验结果 — 记录冲突和依赖问题.

    SPEC 5.5: 校验失败时必须"指出冲突来源"。
    此对象聚合所有冲突记录和依赖问题，供调用方报告或记录日志。

    属性:
        valid: 校验是否通过。
        conflicts: 重复声明冲突记录列表。
        missing_dependencies: 缺失的必需依赖列表。
        circular_dependencies: 循环依赖链列表。
        optional_dependency_issues: 可选依赖声明问题列表。
    """

    valid: bool = True
    conflicts: list[ConflictRecord] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    circular_dependencies: list[tuple[str, ...]] = field(default_factory=list)
    optional_dependency_issues: list[str] = field(default_factory=list)


class ModuleRegistry:
    """模块注册表 — 收集、校验和查询已启用模块.

    SPEC 5.5: Composition Root 中的显式模块清单装配所有 ModuleDefinition，
    ModuleRegistry 在应用启动时执行全量校验。

    使用方式::

        registry = ModuleRegistry()
        registry.register(module_a)
        registry.register(module_b)
        registry.validate()  # 校验失败时抛出异常

    或一次性注册并校验::

        registry = ModuleRegistry.from_modules([module_a, module_b])
        registry.validate()
    """

    def __init__(self) -> None:
        """初始化空注册表。"""

        self._modules: dict[str, ModuleDefinition] = {}
        self._validated: bool = False

    @classmethod
    def from_modules(
        cls,
        modules: list[ModuleDefinition],
    ) -> ModuleRegistry:
        """从模块列表构造注册表.

        参数:
            modules: ModuleDefinition 实例列表。

        返回:
            已注册所有模块的 ModuleRegistry（尚未校验）。
        """

        registry = cls()
        for module in modules:
            registry.register(module)
        return registry

    def register(self, module: ModuleDefinition) -> None:
        """注册一个模块.

        参数:
            module: 待注册的 ModuleDefinition 实例。

        抛出:
            DuplicateDeclarationError: 模块编码已被注册。
        """

        if module.code in self._modules:
            raise DuplicateDeclarationError(
                f"模块编码重复: {module.code}，"
                f"来源: {self._modules[module.code].code} 与 {module.code}",
            )
        self._modules[module.code] = module
        # 注册后需重新校验
        self._validated = False

    @property
    def module_codes(self) -> tuple[str, ...]:
        """返回已注册模块编码列表。"""

        return tuple(self._modules.keys())

    @property
    def modules(self) -> MappingProxyType[str, ModuleDefinition]:
        """返回已注册模块的只读映射。"""

        return MappingProxyType(self._modules)

    def get(self, code: str) -> ModuleDefinition | None:
        """按编码查询模块，不存在返回 None。"""

        return self._modules.get(code)

    def is_validated(self) -> bool:
        """返回注册表是否已通过校验。"""

        return self._validated

    # ── 校验入口 ──────────────────────────────────────────────────────────

    def validate(self) -> ValidationResult:
        """执行全量校验 — SPEC 5.5.

        校验失败时抛出对应的 ``ModuleValidationError`` 子类。
        校验通过时返回 ``ValidationResult``（``valid=True``）。

        SPEC 5.5: "应用启动与 CI 必须失败并指出冲突来源"。
        每类校验失败时异常消息指明具体冲突来源。

        返回:
            校验结果，``valid=True`` 表示通过。

        抛出:
            DuplicateDeclarationError:          声明项重复。
            MissingDependencyError:             必需依赖未启用。
            CircularDependencyError:            依赖构成循环。
            OptionalDependencyNotClosedError:   可选依赖声明不一致。
        """

        result = ValidationResult()

        # 1. 重复声明检测
        self._check_duplicates(result)

        # 2. 依赖关系检测
        self._check_dependencies(result)

        # 校验通过标记
        self._validated = result.valid
        return result

    def validate_or_raise(self) -> None:
        """执行校验，失败时抛出第一个遇到的异常.

        SPEC 5.5: "应用启动与 CI 必须失败并指出冲突来源"。
        此方法在发现第一个问题时立即抛出异常，
        不继续后续校验。适合 CLI 命令的快速失败场景。
        """

        # 1. 重复声明检测（立即抛出）
        self._check_duplicates_or_raise()

        # 2. 必需依赖存在性（立即抛出）
        self._check_required_dependencies_or_raise()

        # 3. 可选依赖声明一致性（立即抛出）
        #    先于循环依赖检测：自引用和声明矛盾是更具体的声明问题。
        self._check_optional_dependencies_or_raise()

        # 4. 循环依赖（立即抛出）
        self._check_circular_dependencies_or_raise()

        self._validated = True

    # ── 重复声明检测 ──────────────────────────────────────────────────────

    def _check_duplicates(self, result: ValidationResult) -> None:
        """检测所有重复声明，汇总到结果中。"""

        # 定义检测维度: (类别名称, 提取函数)
        categories: list[tuple[str, dict[str, list[str]]]] = [
            ("module_code", {}),
            ("api_tag", {}),
            ("permission_code", {}),
            ("error_code", {}),
            ("audit_action", {}),
            ("protected_resource_type", {}),
            ("management_command", {}),
            ("event_code", {}),
            ("handler_code", {}),
            ("initializer_code", {}),
        ]

        for module in self._modules.values():
            self._accumulate(categories[0][1], module.code, [module.code])
            self._accumulate(categories[1][1], module.api_tag, [module.code])
            self._accumulate(
                categories[2][1],
                module.permission_codes,
                [module.code] * len(module.permission_codes),
            )
            self._accumulate(
                categories[3][1],
                module.error_codes,
                [module.code] * len(module.error_codes),
            )
            self._accumulate(
                categories[4][1],
                module.audit_actions,
                [module.code] * len(module.audit_actions),
            )
            self._accumulate(
                categories[5][1],
                module.protected_resource_types,
                [module.code] * len(module.protected_resource_types),
            )
            self._accumulate(
                categories[6][1],
                tuple(cmd.name for cmd in module.management_commands),
                [module.code] * len(module.management_commands),
            )
            self._accumulate(
                categories[7][1],
                module.event_codes,
                [module.code] * len(module.event_codes),
            )
            self._accumulate(
                categories[8][1],
                tuple(h.code for h in module.event_handlers),
                [module.code] * len(module.event_handlers),
            )
            self._accumulate(
                categories[9][1],
                tuple(i.code for i in module.initializers),
                [module.code] * len(module.initializers),
            )

        for cat_name, value_map in categories:
            for value, modules in value_map.items():
                if len(modules) > 1:
                    result.valid = False
                    result.conflicts.append(
                        ConflictRecord(
                            category=cat_name,
                            value=value,
                            modules=tuple(dict.fromkeys(modules)),
                        ),
                    )

    @staticmethod
    def _accumulate(
        target: dict[str, list[str]],
        values: tuple[str, ...] | str,
        sources: list[str],
    ) -> None:
        """将值收集到目标字典中，记录来源模块。"""

        if isinstance(values, str):
            values = (values,)

        for value in values:
            if value not in target:
                target[value] = []
            idx = values.index(value) if isinstance(values, tuple) else 0
            target[value].append(sources[idx])

    def _check_duplicates_or_raise(self) -> None:
        """检测重复声明，发现第一个冲突时立即抛出。"""

        result = ValidationResult()
        self._check_duplicates(result)
        if not result.valid:
            conflict = result.conflicts[0]
            raise DuplicateDeclarationError(
                f"重复声明冲突 [{conflict.category}]: "
                f"值 '{conflict.value}' 被以下模块声明: "
                f"{', '.join(conflict.modules)}",
            )

    # ── 依赖关系检测 ──────────────────────────────────────────────────────

    def _check_dependencies(self, result: ValidationResult) -> None:
        """检测依赖关系: 必需依赖缺失、循环依赖、可选依赖。"""

        module_codes = set(self._modules.keys())

        # 必需依赖缺失检测
        for module in self._modules.values():
            for dep in module.required_dependencies:
                if dep not in module_codes:
                    result.valid = False
                    result.missing_dependencies.append(
                        f"模块 {module.code} 的必需依赖 {dep} 未启用",
                    )

        # 循环依赖检测
        cycles = self._find_cycles()
        for cycle in cycles:
            result.valid = False
            result.circular_dependencies.append(cycle)

        # 可选依赖一致性检测
        self._check_optional_dependencies(result)

    def _check_required_dependencies_or_raise(self) -> None:
        """必需依赖存在性检测，缺失时立即抛出。"""

        module_codes = set(self._modules.keys())
        for module in self._modules.values():
            for dep in module.required_dependencies:
                if dep not in module_codes:
                    raise MissingDependencyError(
                        f"必需依赖未启用: 模块 {module.code} 声明依赖 {dep}，"
                        f"但 {dep} 不在模块清单中",
                    )

    def _check_circular_dependencies_or_raise(self) -> None:
        """循环依赖检测，发现环时立即抛出。"""

        cycles = self._find_cycles()
        if cycles:
            cycle = cycles[0]
            chain = " → ".join(cycle + (cycle[0],))
            raise CircularDependencyError(
                f"依赖构成循环: {chain}",
            )

    def _check_optional_dependencies_or_raise(self) -> None:
        """可选依赖声明一致性检测.

        SPEC 5.5: "可选依赖对应的能力在依赖未启用时必须整体关闭"。

        校验逻辑: 可选依赖允许不在清单中（此时依赖能力被关闭），
        但可选依赖不能指向自身（自引用），且不能仅依赖自身声明的
        可选能力来满足其他模块的必需依赖（声明矛盾）。
        """

        for module in self._modules.values():
            # 可选依赖不能自引用
            if module.code in module.optional_dependencies:
                raise OptionalDependencyNotClosedError(
                    f"模块 {module.code} 声明对自身的可选依赖，违反声明一致性",
                )
            # 必需依赖不能同时出现在可选依赖中（声明矛盾）
            for req_dep in module.required_dependencies:
                if req_dep in module.optional_dependencies:
                    raise OptionalDependencyNotClosedError(
                        f"模块 {module.code} 同时将 {req_dep} 声明为"
                        f"必需依赖和可选依赖，声明矛盾",
                    )

    def _check_optional_dependencies(
        self,
        result: ValidationResult,
    ) -> None:
        """汇总可选依赖声明问题到结果中。"""

        try:
            self._check_optional_dependencies_or_raise()
        except OptionalDependencyNotClosedError as exc:
            result.valid = False
            result.optional_dependency_issues.append(str(exc))

    def _build_dependency_graph(self) -> dict[str, set[str]]:
        """构建依赖图（含必需和可选依赖）.

        返回:
            邻接表: 模块编码 → 其依赖的模块编码集合。
            不在清单中的依赖编码不包含在图中。
        """

        module_codes = set(self._modules.keys())
        graph: dict[str, set[str]] = {}
        for module in self._modules.values():
            deps: set[str] = set()
            for dep in module.required_dependencies:
                if dep in module_codes:
                    deps.add(dep)
            for dep in module.optional_dependencies:
                if dep in module_codes:
                    deps.add(dep)
            graph[module.code] = deps
        return graph

    def _find_cycles(self) -> list[tuple[str, ...]]:
        """在依赖图中检测所有环（拓扑排序）.

        使用 Kahn 算法进行拓扑排序，无法排序的节点构成环。

        返回:
            环列表，每个环为模块编码序列。
        """

        graph = self._build_dependency_graph()

        # 计算入度: 每个节点的入度 = 有多少其他节点依赖它。
        # 依赖图中 node → dep 表示 node 依赖 dep。
        # 在依赖排序中，dep 应在 node 之前出现，
        # 因此入度衡量的是 "被多少节点依赖"。
        in_degree: dict[str, int] = {node: 0 for node in graph}
        for _node, deps in graph.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Kahn 算法
        queue: list[str] = [n for n, d in in_degree.items() if d == 0]
        processed: list[str] = []

        while queue:
            node = queue.pop(0)
            processed.append(node)
            for dep in graph.get(node, set()):
                if dep in in_degree:
                    in_degree[dep] -= 1
                    if in_degree[dep] == 0:
                        queue.append(dep)

        # 未处理的节点构成环
        cyclic_nodes = set(graph.keys()) - set(processed)
        if not cyclic_nodes:
            return []

        # 在环节点中找环路径
        return self._extract_cycles(graph, cyclic_nodes)

    @staticmethod
    def _extract_cycles(
        graph: dict[str, set[str]],
        cyclic_nodes: set[str],
    ) -> list[tuple[str, ...]]:
        """从环节点中提取具体环路径.

        使用 DFS 在环节点子图中寻找环。
        """

        cycles: list[tuple[str, ...]] = []
        visited: set[str] = set()
        stack: list[str] = []

        def dfs(node: str) -> None:
            if node in stack:
                # 找到环
                idx = stack.index(node)
                cycle = tuple(stack[idx:])
                cycles.append(cycle)
                return
            if node in visited:
                return

            visited.add(node)
            stack.append(node)

            for dep in sorted(graph.get(node, set())):
                if dep in cyclic_nodes:
                    dfs(dep)

            stack.pop()

        for node in sorted(cyclic_nodes):
            if node not in visited:
                dfs(node)

        return cycles
