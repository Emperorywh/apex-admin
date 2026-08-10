"""事务内事件处理器 — SPEC 5.7.

SPEC 5.7:
  - 需要与业务数据强一致的处理器作为事务内事件处理器，
    在当前 Unit of Work 提交前同步执行。
  - 任一事务内处理器失败时，整个 Use Case 回滚。
  - 事务内处理器不得执行邮件、Webhook、远程 HTTP 调用或
    其他不可回滚副作用。
  - 事件及处理器通过 ModuleDefinition 显式注册，
    重复事件编码或处理器编码必须使启动和 CI 失败。
  - 多处理器不得依赖执行顺序；稳定排序只用于保证测试和日志可复现。

处理器在 Composition Root 中构造并注入到事件分发器。
每个处理器声明一个全局唯一的 ``code`` 和一个处理的 ``event_code``。
分发器按处理器 code 的稳定排序执行（仅用于测试和日志可复现）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.events.events import DomainEvent


class TransactionalEventHandler(ABC):
    """事务内事件处理器抽象基类 — SPEC 5.7.

    SPEC 5.7: "需要与业务数据强一致的处理器作为事务内事件处理器，
    在当前 Unit of Work 提交前同步执行"。

    处理器在 Composition Root 构造时注入所需依赖（如 Repository）。
    ``handle`` 方法在事件分发时被同步调用，处理器在当前事务内
    执行业务逻辑。任一处理器抛出异常时，整个 Use Case 回滚。

    SPEC 5.7 约束:
      - 处理器不得执行邮件、Webhook、远程 HTTP 调用或其他不可回滚副作用。
      - 处理器不得依赖执行顺序（稳定排序仅用于测试和日志可复现）。

    子类必须实现:
      - ``code``: 全局唯一的处理器编码。
      - ``event_code``: 此处理器处理的事件编码。
      - ``handle``: 在当前事务内处理事件。
    """

    @property
    @abstractmethod
    def code(self) -> str:
        """全局唯一的处理器编码.

        SPEC 5.7: "重复事件编码或处理器编码必须使启动和 CI 失败"。
        处理器编码格式为 ``<MODULE>.<ACTION>``，仅大写字母、数字和下划线。
        """

    @property
    @abstractmethod
    def event_code(self) -> str:
        """此处理器处理的事件编码（如 ``USER.CREATED``）."""

    @abstractmethod
    async def handle(
        self,
        event: DomainEvent,
        session: AsyncSession,
    ) -> None:
        """在当前事务内同步处理事件.

        SPEC 5.7: 处理器在 UoW 提交前同步执行。
        处理器失败（抛出异常）时，整个 Use Case 回滚。

        参数:
            event:  被分发的事件实例。
            session: 当前 UoW 拥有的 AsyncSession，
                     处理器在事务内执行数据库操作。
        """
