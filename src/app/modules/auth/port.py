"""认证模块 Repository Port — SPEC 5.2 / 5.6 / 12.2 / 12.3 / 12.4.

SPEC 5.2: "Repository、Unit of Work、文件存储和外部服务 Port
由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port。

会话 Port 和登录失败计数 Port 分离，各自只暴露所需方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.auth.models import LoginAttempt, Session


class SessionRepository(ABC):
    """会话 Repository Port — 会话持久化抽象接口.

    SPEC 12.3: "会话信息持久化到 PostgreSQL"。
    SPEC 12.2: 通过 Access Token 摘要查找会话。

    Port 方法签名不包含 SQLAlchemy 类型，确保内层不感知具体 ORM
    （SPEC 5.2 / 8.1）。返回值为领域实体 ``Session``，不是 ORM 模型。
    """

    @abstractmethod
    async def add(self, session: Session) -> None:
        """添加新会话到当前事务.

        参数:
            session: 待添加的会话领域实体。
        """

    @abstractmethod
    async def get_by_token_digest(self, digest: str) -> Session | None:
        """按 Access Token 摘要查找会话 — SPEC 12.2 / 12.3.

        认证依赖通过此方法在每请求查库校验会话有效性。

        参数:
            digest: Access Token 的 HMAC-SHA-256 摘要。

        返回:
            会话领域实体；不存在时返回 None。
        """

    @abstractmethod
    async def get_by_id(self, session_id: UUID, user_id: UUID) -> Session | None:
        """按 ID 查找会话（限定用户）.

        退出其他会话时使用，确保只能操作本人会话。

        参数:
            session_id: 会话 ID。
            user_id:    当前用户 ID（安全约束）。

        返回:
            会话领域实体；不存在或不属于该用户时返回 None。
        """

    @abstractmethod
    async def list_active_by_user(self, user_id: UUID) -> list[Session]:
        """查询用户的活动会话列表 — SPEC 12.3.

        返回该用户所有未吊销的会话。

        参数:
            user_id: 用户 ID。

        返回:
            活动会话列表（按创建时间降序）。
        """

    @abstractmethod
    async def revoke(
        self,
        session_id: UUID,
        *,
        reason: str,
    ) -> bool:
        """吊销会话 — SPEC 12.3.

        将指定会话标记为已吊销。已吊销会话不可继续使用。

        参数:
            session_id: 会话 ID。
            reason:     吊销原因。

        返回:
            吊销成功返回 True；会话不存在返回 False。
        """

    @abstractmethod
    async def revoke_all_by_user(
        self,
        user_id: UUID,
        *,
        reason: str,
    ) -> int:
        """吊销用户全部活动会话 — SPEC 12.3.

        用于用户禁用或管理员重置密码事件的事务内处理器。

        参数:
            user_id: 用户 ID。
            reason:  吊销原因。

        返回:
            被吊销的会话数量。
        """

    @abstractmethod
    async def update_activity(
        self,
        session_id: UUID,
        *,
        last_activity_at: object,
    ) -> None:
        """更新最近活动时间 — SPEC 12.3.

        SPEC 12.3: "最近活动时间最多每 5 分钟条件更新一次"。
        调用方负责判断是否满足更新间隔，此方法仅执行写入。

        参数:
            session_id:        会话 ID。
            last_activity_at:  新的最近活动时间（UTC）。
        """

    @abstractmethod
    async def revoke_all_by_user_with_session(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        reason: str,
    ) -> int:
        """在指定 session 上吊销用户全部活动会话（事务内事件处理器用）.

        SPEC 5.7: 事务内事件处理器在当前 UoW 的 AsyncSession 上执行，
        保证与业务数据强一致。

        参数:
            session:  当前事务的 AsyncSession。
            user_id:  用户 ID。
            reason:   吊销原因。

        返回:
            被吊销的会话数量。
        """


class LoginAttemptRepository(ABC):
    """登录失败计数 Repository Port — SPEC 12.4.

    SPEC 12.4: "登录失败状态持久化到 PostgreSQL，以规范化账号标识和
    可信客户端 IP 作为独立维度统计"。

    两个维度独立计数:
      - 账号维度: 连续失败 5 次限制 15 分钟，成功登录清理。
      - IP 维度: 连续失败 20 次限制 15 分钟，到期自动解除，成功不清理。
    """

    @abstractmethod
    async def get(self, dimension: str, key: str) -> LoginAttempt | None:
        """查询指定维度的失败计数记录.

        参数:
            dimension: 维度标识（``"account"`` 或 ``"ip"``）。
            key:       维度键值。

        返回:
            失败计数实体；不存在返回 None。
        """

    @abstractmethod
    async def record_failure(
        self,
        dimension: str,
        key: str,
        *,
        failed_at: object,
    ) -> int:
        """记录一次失败并返回更新后的连续失败次数.

        如果记录不存在则创建（count=1），否则递增 count。
        仅递增计数和更新 ``last_failed_at``，不设置锁定。

        参数:
            dimension: 维度标识。
            key:       维度键值。
            failed_at: 本次失败时间（UTC）。

        返回:
            更新后的连续失败次数。
        """

    @abstractmethod
    async def lock(
        self,
        dimension: str,
        key: str,
        *,
        locked_until: object,
    ) -> None:
        """设置指定维度的锁定截止时间 — SPEC 12.4.

        由 Use Case 在失败次数达到阈值时调用，与 ``record_failure`` 分离，
        避免重复递增计数。

        参数:
            dimension:    维度标识。
            key:          维度键值。
            locked_until: 锁定截止时间（UTC）。
        """

    @abstractmethod
    async def reset(self, dimension: str, key: str) -> None:
        """重置指定维度的失败计数 — SPEC 12.4.

        SPEC 12.4: "成功登录后清理该账号失败状态"。
        仅对账号维度调用；IP 维度不清理。

        参数:
            dimension: 维度标识。
            key:       维度键值。
        """
