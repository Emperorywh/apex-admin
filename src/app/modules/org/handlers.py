"""组织模块事务内事件处理器 — SPEC 5.7 / 14.3.

SPEC 5.7:
  - 需要与业务数据强一致的处理器作为事务内事件处理器，
    在当前 Unit of Work 提交前同步执行。
  - 任一事务内处理器失败时，整个 Use Case 回滚。

SPEC 14.3:
  - "用户离职或禁用时组织关系按规则处理"。
  - 规则：用户被禁用时，清除其全部组织关系（主部门 + 岗位）。
    这样禁用用户的组织关系不会被遗留，当用户重新启用时需重新设置。

处理器在 identity 模块的禁用 Use Case 事务内被调用，
在当前 AsyncSession 上执行 DELETE 清除组织关系，保证与业务数据强一致
（SPEC 5.7: 同提交、同回滚）。

处理器通过 Composition Root（identity Router 的依赖注入函数）注入到
identity Use Case 的事件分发器中。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.events.handlers import TransactionalEventHandler
from app.modules.org.adapter import SqlAlchemyOrgRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.events.events import DomainEvent


class ClearUserOrgRelationsOnDisabled(TransactionalEventHandler):
    """用户禁用时清除全部组织关系 — SPEC 14.3 / 5.7.

    SPEC 14.3: "用户离职或禁用时组织关系按规则处理"。
    监听 ``USER.DISABLED`` 事件，在当前事务内清除该用户的全部
    组织关系（主部门关系 + 岗位关系）。

    SPEC 5.7: 处理器失败时整个 Use Case 回滚——如果清除组织关系失败，
    用户禁用操作也回滚，保证一致性。

    规则说明（写入文档 docs/org-relation-rules.md）:
      - 用户被禁用时，其主部门关系和岗位关系被清除。
      - 组织关系清除后不可恢复——用户重新启用后需重新设置组织关系。
      - 审计记录不受影响（审计日志不可变，SPEC 18.2）。
    """

    @property
    def code(self) -> str:
        """全局唯一的处理器编码."""

        return "ORG.CLEAR_USER_ORG_ON_DISABLED"

    @property
    def event_code(self) -> str:
        """处理的事件编码."""

        return "USER.DISABLED"

    async def handle(self, event: DomainEvent, session: AsyncSession) -> None:
        """在当前事务内清除用户全部组织关系.

        事件载荷中的 ``user_id`` 为被禁用用户的 UUID 字符串。
        通过 OrgRepository Adapter 执行 DELETE 操作。
        """

        user_id_str = event.payload.get("user_id", "")
        if not user_id_str:
            return

        from uuid import UUID

        user_id: UUID = UUID(user_id_str)
        repo = SqlAlchemyOrgRepository(session)
        await repo.clear_user_org_relations(user_id)
