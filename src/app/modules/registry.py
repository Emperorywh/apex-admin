"""模块注册表与启动校验（SPEC §5.5）。

:class:`ModuleRegistry` 接收显式模块清单，在构造时执行以下校验：

1. 模块编码全局唯一。
2. 路由（HTTP method + path）全局唯一。
3. 权限点编码全局唯一。
4. 错误码全局唯一。
5. 审计动作编码全局唯一。
6. 资源类型编码全局唯一。
7. 事件编码全局唯一。
8. 事件处理器编码全局唯一。
9. 命令编码全局唯一。
10. 必需依赖已启用。
11. 依赖图无环。

任一校验失败时抛出 :class:`ModuleRegistrationError`，
使应用启动失败并指出冲突来源（SPEC §5.5）。

Composition Root 负责提供显式模块清单，ModuleRegistry 在
``create_app`` 或 ``modules validate`` 命令中被调用。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.modules.contract import ModuleDefinition


class ModuleRegistrationError(Exception):
    """模块注册校验失败（SPEC §5.5）。

    校验失败时抛出此异常，使应用启动失败。
    异常消息指明冲突来源和具体冲突项，便于定位问题。
    """


@dataclass(frozen=True)
class RegisteredModule:
    """已注册模块的运行时视图。

    包含原始 :class:`ModuleDefinition` 和注册表计算的附加信息
    （哪些可选依赖已满足、哪些未满足）。

    模块通过检查 :attr:`unsatisfied_optional_dependencies` 决定
    是否关闭可选依赖对应的能力（SPEC §5.5：可选依赖未启用时其能力整体关闭）。

    Attributes:
        definition: 原始模块定义
        unsatisfied_optional_dependencies: 声明了但未启用的可选依赖编码集合
    """

    definition: ModuleDefinition
    unsatisfied_optional_dependencies: frozenset[str]


class ModuleRegistry:
    """模块注册表（SPEC §5.5）。

    接收显式模块清单，在构造时执行全部校验。
    校验通过后，提供模块查询和初始化器收集功能。

    Usage::

        from app.composition_root import get_enabled_modules
        from app.modules.registry import ModuleRegistry

        modules = get_enabled_modules()
        registry = ModuleRegistry(modules)  # 校验失败时抛出异常
    """

    def __init__(self, modules: list[ModuleDefinition]) -> None:
        """构造注册表并执行全部校验。

        Args:
            modules: 显式模块清单（由 Composition Root 提供）

        Raises:
            ModuleRegistrationError: 任一校验失败
        """
        # 校验按 SPEC §5.5 顺序依次执行
        self._modules_by_code: dict[str, ModuleDefinition] = {}
        self._registered: list[RegisteredModule] = []
        self._validate(modules)

    # ------------------------------------------------------------------
    # 公开查询接口
    # ------------------------------------------------------------------

    @property
    def modules(self) -> list[ModuleDefinition]:
        """返回已注册模块列表，按注册顺序排列。"""
        return [rm.definition for rm in self._registered]

    @property
    def registered_modules(self) -> list[RegisteredModule]:
        """返回已注册模块的运行时视图列表。"""
        return list(self._registered)

    def get_module(self, code: str) -> ModuleDefinition | None:
        """按模块编码查询模块定义。

        Returns:
            匹配的 ModuleDefinition；编码不存在时返回 None
        """
        module = self._modules_by_code.get(code)
        return module

    def is_dependency_satisfied(self, module_code: str, dependency_code: str) -> bool:
        """检查指定模块的某个依赖是否已启用。

        用于模块判断可选依赖能力是否应启用（SPEC §5.5）。

        Args:
            module_code: 依赖方模块编码
            dependency_code: 被依赖模块编码

        Returns:
            依赖已启用返回 True；模块不存在或依赖未启用返回 False
        """
        return dependency_code in self._modules_by_code

    def get_unsatisfied_optional_dependencies(self, module_code: str) -> frozenset[str]:
        """返回指定模块中未启用的可选依赖编码集合。

        模块据此关闭对应能力（SPEC §5.5：可选依赖未启用时其能力整体关闭）。

        Returns:
            未启用的可选依赖编码集合；模块不存在时返回空集合
        """
        for rm in self._registered:
            if rm.definition.code == module_code:
                return rm.unsatisfied_optional_dependencies
        return frozenset()

    # ------------------------------------------------------------------
    # 校验逻辑
    # ------------------------------------------------------------------

    def _validate(self, modules: list[ModuleDefinition]) -> None:
        """执行全部注册校验（SPEC §5.5）。"""
        self._check_duplicate_module_codes(modules)
        self._check_duplicate_routes(modules)
        self._check_duplicate_codes(
            modules,
            extract_fn=lambda m: {p.code for p in m.permission_points},
            label="权限点",
        )
        self._check_duplicate_codes(
            modules,
            extract_fn=lambda m: {e.code for e in m.error_codes},
            label="错误码",
        )
        self._check_duplicate_codes(
            modules,
            extract_fn=lambda m: {a.code for a in m.audit_actions},
            label="审计动作",
        )
        self._check_duplicate_codes(
            modules,
            extract_fn=lambda m: {r.code for r in m.resource_types},
            label="资源类型",
        )
        self._check_duplicate_codes(
            modules,
            extract_fn=lambda m: {e.code for e in m.events},
            label="事件",
        )
        self._check_duplicate_codes(
            modules,
            extract_fn=lambda m: {h.code for h in m.event_handlers},
            label="事件处理器",
        )
        self._check_duplicate_codes(
            modules,
            extract_fn=lambda m: {c.code for c in m.commands},
            label="命令",
        )
        self._check_required_dependencies(modules)
        self._check_dependency_cycles(modules)

        # 全部校验通过后构建内部状态
        self._build_state(modules)

    def _check_duplicate_module_codes(self, modules: list[ModuleDefinition]) -> None:
        """校验模块编码全局唯一。"""
        seen: dict[str, str] = {}
        for module in modules:
            if module.code in seen:
                raise ModuleRegistrationError(
                    f"重复模块编码 '{module.code}'："
                    f"'{seen[module.code]}' 与 '{module.name}' 同时声明"
                )
            seen[module.code] = module.name

    def _check_duplicate_codes(
        self,
        modules: list[ModuleDefinition],
        extract_fn: Callable[[ModuleDefinition], set[str]],
        label: str,
    ) -> None:
        """通用编码重复检测。

        Args:
            modules: 全部模块定义
            extract_fn: 从模块定义中提取编码集合的函数
            label: 冲突描述标签（例如"权限点"）
        """
        seen: dict[str, str] = {}
        for module in modules:
            for code in extract_fn(module):
                if code in seen:
                    raise ModuleRegistrationError(
                        f"重复{label}编码 '{code}'："
                        f"模块 '{seen[code]}' 与模块 '{module.code}' 同时声明"
                    )
                seen[code] = module.code

    def _check_duplicate_routes(self, modules: list[ModuleDefinition]) -> None:
        """校验路由（HTTP method + path）全局唯一。"""
        seen: dict[tuple[str, str], str] = {}
        for module in modules:
            for router in module.routers:
                for method, path in _extract_routes(router):
                    key = (method, path)
                    if key in seen:
                        raise ModuleRegistrationError(
                            f"重复路由 {method} {path}："
                            f"模块 '{seen[key]}' 与模块 '{module.code}' 同时声明"
                        )
                    seen[key] = module.code

    def _check_required_dependencies(self, modules: list[ModuleDefinition]) -> None:
        """校验必需依赖已启用（SPEC §5.5）。"""
        registered_codes = {m.code for m in modules}
        for module in modules:
            missing = module.required_dependencies - registered_codes
            if missing:
                missing_str = ", ".join(sorted(missing))
                raise ModuleRegistrationError(
                    f"模块 '{module.code}' 的必需依赖未启用：{missing_str}"
                )

    def _check_dependency_cycles(self, modules: list[ModuleDefinition]) -> None:
        """校验依赖图无环（SPEC §5.5）。

        使用三色 DFS 检测有向图中的循环。
        """
        modules_by_code = {m.code: m for m in modules}
        # 颜色：WHITE=未访问, GRAY=访问中, BLACK=已完成
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {code: WHITE for code in modules_by_code}

        def dfs(code: str, path: list[str]) -> None:
            color[code] = GRAY
            module = modules_by_code[code]
            deps = module.required_dependencies | module.optional_dependencies
            for dep in sorted(deps):
                if dep not in modules_by_code:
                    continue
                if color[dep] == GRAY:
                    # 发现回边 → 循环
                    cycle = path[path.index(dep) :] + [code, dep]
                    chain = " → ".join(cycle)
                    raise ModuleRegistrationError(f"模块依赖构成循环：{chain}")
                if color[dep] == WHITE:
                    dfs(dep, path + [code])
            color[code] = BLACK

        for code in modules_by_code:
            if color[code] == WHITE:
                dfs(code, [])

    def _build_state(self, modules: list[ModuleDefinition]) -> None:
        """校验通过后构建内部查询状态。"""
        registered_codes = {m.code for m in modules}
        for module in modules:
            self._modules_by_code[module.code] = module
            unsatisfied = module.optional_dependencies - registered_codes
            self._registered.append(
                RegisteredModule(
                    definition=module,
                    unsatisfied_optional_dependencies=unsatisfied,
                )
            )


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

# 避免在类内导入 Callable 导致类型循环
from collections.abc import Callable  # noqa: E402


def _extract_routes(router: APIRouter) -> set[tuple[str, str]]:
    """从 APIRouter 提取所有 (HTTP method, path) 对。

    遍历路由器的路由列表，提取每个 HTTP 路由的方法和路径。
    WebSocket 路由和其他非 HTTP 路由不参与重复检测。

    Args:
        router: FastAPI APIRouter 实例

    Returns:
        (method, path) 对的集合
    """
    routes: set[tuple[str, str]] = set()
    for route in router.routes:
        # APIRoute 是 HTTP 路由，包含 methods 和 path 属性
        if isinstance(route, APIRoute) and route.methods is not None:
            for method in route.methods:
                routes.add((method, route.path))
    return routes
