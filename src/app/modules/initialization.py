"""幂等初始化框架（SPEC §8.5）。

从注册的模块收集所有初始化器，在独立的 Unit of Work 中依次执行。
初始化器使用稳定自然键执行幂等 upsert，可重复执行不产生重复数据
（SPEC §8.5）。

初始化框架由 G1 提供，各模块通过 :class:`~app.modules.contract.ModuleDefinition`
注册本模块初始化器。

- G2 的身份模块负责首个管理员和基础权限点初始化（SPEC §8.5）。
- G3 的菜单与字典模块分别负责基础菜单和系统字典初始化（SPEC §8.5）。

每个初始化器只能写入本模块拥有的数据（SPEC §8.5）。
初始化密码不得硬编码、写入命令历史或输出到日志（SPEC §8.5）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.modules.registry import ModuleRegistry
from app.ports.unit_of_work import UnitOfWork

_logger = logging.getLogger("app.modules.initialization")


# Unit of Work 工厂类型：创建新的工作单元实例
UowFactory = Callable[[], UnitOfWork]


class InitializationRunner:
    """幂等初始化执行器（SPEC §8.5）。

    从注册表收集所有模块的初始化器，在各自的 Unit of Work 中执行。
    可重复调用 :meth:`run_all`——幂等性由各初始化器自身保证
    （SPEC §8.5：初始化过程可重复执行且不会创建重复数据）。

    每个初始化器在独立的 Unit of Work 中执行并自动提交，
    确保初始化数据持久化。失败时立即停止，后续初始化器不再执行。

    Usage::

        runner = InitializationRunner(registry, db_pool_provider.create_unit_of_work)
        await runner.run_all()
    """

    def __init__(
        self,
        registry: ModuleRegistry,
        uow_factory: UowFactory,
    ) -> None:
        """初始化执行器。

        Args:
            registry: 已校验的模块注册表
            uow_factory: Unit of Work 工厂，每次调用返回新的 UoW 实例
        """
        self._registry = registry
        self._uow_factory = uow_factory

    async def run_all(self) -> None:
        """执行所有已注册模块的初始化器。

        按 :class:`ModuleDefinition` 注册顺序遍历模块，
        每个模块内按声明顺序执行初始化器。
        每个初始化器在独立的 Unit of Work 中执行并提交。

        失败时立即停止并抛出异常，后续初始化器不再执行。
        """
        for module in self._registry.modules:
            for initializer in module.initializers:
                _logger.info(
                    "执行初始化器",
                    extra={
                        "module_code": module.code,
                        "initializer_code": initializer.code,
                    },
                )
                # 每个初始化器在独立的 Unit of Work 中执行（SPEC §8.5）
                # UoW 在 __aexit__ 时自动提交（无异常）或回滚（有异常）
                async with self._uow_factory() as uow:
                    await initializer.run(uow)
