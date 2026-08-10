"""幂等初始化框架 — SPEC 8.5.

SPEC 8.5:
  - 初始化框架由 G1 提供，各模块通过 ModuleDefinition 注册本模块初始化器。
  - 初始化器使用稳定自然键或稳定编码执行幂等 upsert，
    不得按显示名称判断重复。
  - 初始化过程可重复执行且不会创建重复数据。
  - 初始化器只能写入本模块拥有的数据。

初始化器（``Initializer``）由各模块实现，声明全局唯一的稳定编码。
执行器（``InitializationRunner``）在数据库事务内按稳定顺序执行
所有初始化器，保证可重复执行且不产生重复数据。

SPEC 8.5: "初始化器使用稳定自然键或稳定编码执行幂等 upsert，
不得按显示名称判断重复"。每个初始化器的 ``initialize`` 方法自行
实现幂等 upsert 逻辑（如 INSERT ... ON CONFLICT DO UPDATE），
以稳定自然键作为唯一约束判断。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class Initializer(ABC):
    """幂等初始化器抽象基类 — SPEC 8.5.

    SPEC 8.5: "初始化器使用稳定自然键或稳定编码执行幂等 upsert，
    不得按显示名称判断重复"。

    每个初始化器声明:
      - ``code``: 全局唯一的稳定编码，标识此初始化器。
      - ``initialize``: 执行幂等 upsert 逻辑。

    幂等性要求: 多次执行 ``initialize`` 产生与一次执行相同的结果，
    不会创建重复数据（SPEC 8.5: "初始化过程可重复执行且不会创建重复数据"）。
    初始化器只能写入本模块拥有的数据
    （SPEC 8.5: "初始化器只能写入本模块拥有的数据"）。

    子类应使用数据库唯一约束和 ``ON CONFLICT`` 语义实现幂等 upsert。
    """

    @property
    @abstractmethod
    def code(self) -> str:
        """全局唯一的初始化器编码.

        编码格式为 ``<MODULE>.<NAME>``，仅大写字母、数字和下划线。
        Composition Root 收集所有模块的初始化器后检查编码唯一性。
        """

    @abstractmethod
    async def initialize(self, session: AsyncSession) -> None:
        """在数据库事务内执行幂等 upsert.

        SPEC 8.5: "初始化器使用稳定自然键或稳定编码执行幂等 upsert"。

        实现必须满足:
          - 可重复执行，不创建重复数据。
          - 只写入本模块拥有的数据。
          - 使用稳定自然键（非显示名称）判断重复。

        参数:
            session: 当前事务的 AsyncSession。
        """


class InitializationRunner:
    """初始化执行器 — 在事务内按稳定顺序执行初始化器.

    SPEC 8.5: "初始化框架由 G1 提供，各模块通过 ModuleDefinition
    注册本模块初始化器"。

    执行器由 Composition Root 构造：收集所有已启用模块的初始化器，
    按编码稳定排序后逐个执行。所有初始化器在同一事务内执行，
    任一失败则整体回滚。

    使用方式::

        runner = InitializationRunner(initializers)
        async with uow:
            await runner.run(uow.session)
            await uow.commit()
    """

    def __init__(self, initializers: list[Initializer]) -> None:
        """初始化执行器，按编码稳定排序.

        参数:
            initializers: 已启用模块提供的初始化器列表。
        """

        # 按编码稳定排序，保证可复现性。
        self._initializers: list[Initializer] = sorted(
            initializers,
            key=lambda i: i.code,
        )

    @property
    def initializers(self) -> list[Initializer]:
        """返回已注册的初始化器列表（稳定排序后的只读副本）。"""

        return list(self._initializers)

    async def run(self, session: AsyncSession) -> None:
        """在当前事务内按稳定顺序执行所有初始化器.

        SPEC 8.5: "初始化过程可重复执行且不会创建重复数据"。
        所有初始化器在同一事务内执行，任一失败则整体回滚。

        参数:
            session: 当前事务的 AsyncSession。
        """

        for initializer in self._initializers:
            await initializer.initialize(session)
