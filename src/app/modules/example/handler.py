"""示例事务内事件处理器 — SPEC 5.7.

SPEC 5.7:
  - 需要与业务数据强一致的处理器作为事务内事件处理器，
    在当前 Unit of Work 提交前同步执行。
  - 任一事务内处理器失败时，整个 Use Case 回滚。
  - 事务内处理器不得执行邮件、Webhook、远程 HTTP 调用或
    其他不可回滚副作用。

此处理器演示事务内事件处理的完整模式：监听 ``EXAMPLE.ITEM_CREATED``
事件，在创建条目的同一事务内更新条目的描述，标记已被事件处理器处理。
处理器失败时整个事务回滚（SPEC 5.7: "任一事务内处理器失败时，
整个 Use Case 回滚"）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import update

from app.core.events.handlers import TransactionalEventHandler
from app.modules.example.orm import ExampleItemORM

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.events.events import DomainEvent


class ExampleItemCreatedHandler(TransactionalEventHandler):
    """处理 ``EXAMPLE.ITEM_CREATED`` 事件 — 在事务内更新条目处理标记.

    此处理器演示事务内事件处理模式。当条目创建时，在同一个事务内
    将条目描述追加处理标记 ``[processed]``，证明事件处理器在
    UoW 提交前同步执行且与业务数据强一致（SPEC 5.7）。

    处理器失败（如条目不存在）时抛出异常，整个事务回滚
    （SPEC 5.7: "任一事务内处理器失败时，整个 Use Case 回滚"）。
    """

    @property
    def code(self) -> str:
        """全局唯一的处理器编码。"""

        return "EXAMPLE.MARK_CREATED"

    @property
    def event_code(self) -> str:
        """此处理器处理的事件编码。"""

        return "EXAMPLE.ITEM_CREATED"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        """在事务内处理条目创建事件.

        SPEC 5.7: 处理器在 UoW 提交前同步执行。
        更新条目描述追加处理标记，与业务数据在同一事务提交。

        参数:
            event:   ``ExampleItemCreated`` 事件实例。
            session: 当前 UoW 拥有的 AsyncSession。
        """

        item_id_str = event.payload.get("item_id", "")
        if not item_id_str:
            return

        item_id = UUID(str(item_id_str))
        await session.execute(
            update(ExampleItemORM)
            .where(ExampleItemORM.id == item_id)
            .values(description="[processed]"),
        )
