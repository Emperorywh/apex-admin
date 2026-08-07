"""会话生命周期管理端口（SPEC §12.3）。

跨模块端口，允许其他模块（如用户模块）在同一事务中触发会话吊销。

用户禁用、管理员重置密码和用户自助修改密码时，用户模块通过此端口
在当前事务内吊销相关会话（SPEC §12.3）。

此端口在当前 :class:`~app.ports.unit_of_work.UnitOfWork` 的事务作用域内
执行，确保用户状态变更和会话吊销原子提交（SPEC §5.6：同一 Use Case
内禁止跨多个 Unit of Work）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.ports.unit_of_work import UnitOfWork


class SessionLifecyclePort(ABC):
    """会话生命周期管理端口（SPEC §12.3）。

    由认证模块实现，供用户模块在密码变更和用户禁用时调用，
    在同一事务内吊销相关会话。
    """

    @abstractmethod
    async def revoke_all_user_sessions(
        self,
        uow: UnitOfWork,
        user_id: UUID,
        reason: str,
        current_time: datetime,
    ) -> int:
        """吊销用户全部活跃会话（SPEC §12.3）。

        在当前事务内将用户的所有活跃会话标记为已吊销，删除关联的
        Access Token 并标记 Refresh Token 为已吊销。

        Args:
            uow: 当前 Unit of Work（操作在同一个事务中完成）
            user_id: 目标用户 UUID
            reason: 吊销原因（如 ``user_disabled``、``password_reset``）
            current_time: 当前 UTC 时间

        Returns:
            被吊销的会话数量
        """

    @abstractmethod
    async def revoke_user_sessions_except(
        self,
        uow: UnitOfWork,
        user_id: UUID,
        keep_session_id: UUID,
        reason: str,
        current_time: datetime,
    ) -> int:
        """吊销用户除指定会话外的全部活跃会话（SPEC §12.3）。

        用于用户自助修改密码场景：保留当前会话，吊销其他会话。

        Args:
            uow: 当前 Unit of Work
            user_id: 目标用户 UUID
            keep_session_id: 要保留的会话 UUID
            reason: 吊销原因
            current_time: 当前 UTC 时间

        Returns:
            被吊销的会话数量
        """
