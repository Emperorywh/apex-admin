"""事件处理器注册表（SPEC §5.7）。

从已校验的 :class:`~app.modules.registry.ModuleRegistry` 收集所有事务内
事件处理器，按事件编码索引，供 :class:`~app.events.dispatcher.TransactionalEventDispatcher`
在提交前调度。

处理器声明来源仅为 :attr:`~app.modules.contract.ModuleDefinition.event_handlers`
（通过 ModuleDefinition 显式注册）。处理器函数实现由 Composition Root 提供，
注册表验证声明与实现一一对应（SPEC §5.5、§5.7：禁止自动发现）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.events.base import TransactionalEventHandlerFn
from app.modules.registry import ModuleRegistry


class EventHandlerRegistrationError(Exception):
    """事件处理器注册校验失败（SPEC §5.7）。

    当声明的事务内处理器缺少实现函数，或提供的实现未在 ModuleDefinition
    中声明时抛出此异常，使应用启动失败。
    """


@dataclass(frozen=True)
class RegisteredHandler:
    """已注册的事务内事件处理器运行时视图。

    Attributes:
        code: 处理器编码（全局唯一）。
        event_code: 此处理器处理的事件编码。
        run: 处理器异步函数，在 UoW 提交前同步执行。
    """

    code: str
    event_code: str
    run: TransactionalEventHandlerFn


class EventHandlerRegistry:
    """事件处理器注册表（SPEC §5.7）。

    从已校验的 :class:`ModuleRegistry` 和处理器函数实现映射构建。
    仅注册 ``transactional=True`` 的处理器——事务后处理器属于持久化任务
    扩展（EXT），不在 G1 注册（SPEC §5.7）。

    注册表验证：

    - 每个声明的事务内处理器都有对应的实现函数。
    - 每个提供的实现函数都有对应的事务内处理器声明。

    Usage::

        registry = EventHandlerRegistry(module_registry, handler_fns)
        handlers = registry.get_handlers("user.created")

    Args:
        module_registry: 已校验的模块注册表。
        handler_implementations: 处理器编码到异步函数的映射。
    """

    def __init__(
        self,
        module_registry: ModuleRegistry,
        handler_implementations: dict[str, TransactionalEventHandlerFn],
    ) -> None:
        self._handlers_by_event: dict[str, list[RegisteredHandler]] = {}
        self._build(module_registry, handler_implementations)

    # ------------------------------------------------------------------
    # 公开查询接口
    # ------------------------------------------------------------------

    def get_handlers(self, event_code: str) -> list[RegisteredHandler]:
        """返回指定事件编码的事务内处理器列表。

        返回的列表按处理器编码稳定排序，仅用于测试和日志可复现
        （SPEC §5.7：多处理器执行顺序不保证）。

        Returns:
            处理器列表的副本；无匹配时返回空列表。
        """
        return list(self._handlers_by_event.get(event_code, ()))

    @property
    def event_codes(self) -> frozenset[str]:
        """已注册处理器监听的事件编码集合。"""
        return frozenset(self._handlers_by_event)

    @property
    def handler_codes(self) -> frozenset[str]:
        """全部已注册处理器编码集合。"""
        return frozenset(
            handler.code for bucket in self._handlers_by_event.values() for handler in bucket
        )

    # ------------------------------------------------------------------
    # 构建逻辑
    # ------------------------------------------------------------------

    def _build(
        self,
        module_registry: ModuleRegistry,
        handler_implementations: dict[str, TransactionalEventHandlerFn],
    ) -> None:
        """从模块声明和实现映射构建注册表。

        收集所有事务内处理器声明，验证与实现映射一一对应，
        然后按事件编码索引并按处理器编码稳定排序。
        """
        # 收集所有声明的事务内处理器：handler_code → event_code
        declared_transactional: dict[str, str] = {}
        for module in module_registry.modules:
            for handler_def in module.event_handlers:
                if handler_def.transactional:
                    declared_transactional[handler_def.code] = handler_def.event_code

        # 验证声明与实现一一对应
        declared_codes = set(declared_transactional)
        impl_codes = set(handler_implementations)

        missing_impls = sorted(declared_codes - impl_codes)
        if missing_impls:
            raise EventHandlerRegistrationError(
                f"以下事务内事件处理器已声明但未提供实现函数：{', '.join(missing_impls)}"
            )

        undeclared_impls = sorted(impl_codes - declared_codes)
        if undeclared_impls:
            raise EventHandlerRegistrationError(
                f"以下处理器实现未在 ModuleDefinition 中声明为事务内处理器："
                f"{', '.join(undeclared_impls)}"
            )

        # 按事件编码索引处理器
        for handler_code, event_code in declared_transactional.items():
            fn = handler_implementations[handler_code]
            entry = RegisteredHandler(code=handler_code, event_code=event_code, run=fn)
            self._handlers_by_event.setdefault(event_code, []).append(entry)

        # 按处理器编码稳定排序（SPEC §5.7：多处理器执行顺序不保证；
        # 稳定排序仅用于测试和日志可复现）
        for bucket in self._handlers_by_event.values():
            bucket.sort(key=lambda h: h.code)
