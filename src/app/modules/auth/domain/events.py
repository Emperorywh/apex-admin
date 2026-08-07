"""认证模块领域事件（SPEC §5.7、§12）。

领域事件是不依赖 FastAPI、ORM 和基础设施的不可变对象。
跨模块事件载荷只允许稳定编码、标量值和资源 ID（SPEC §5.7）。

认证模块发布 ``auth.session.created`` 和 ``auth.session.revoked`` 事件，
供其他模块（如审计模块）在事务内响应。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.events.base import DomainEvent


@dataclass(frozen=True)
class SessionCreated(DomainEvent):
    """会话创建事件。

    在登录成功、会话创建后由 Use Case 发布，事务内事件处理器在
    Unit of Work 提交前同步执行（SPEC §5.7）。

    Attributes:
        occurred_at: 事件发生时间（UTC），继承自 :class:`DomainEvent`
        session_id: 新建会话的 UUID
        user_id: 登录用户的 UUID
    """

    code: ClassVar[str] = "auth.session.created"

    session_id: UUID
    user_id: UUID


@dataclass(frozen=True)
class SessionRevoked(DomainEvent):
    """会话吊销事件。

    在登出或强制下线时由 Use Case 发布（SPEC §12.3）。

    Attributes:
        occurred_at: 事件发生时间（UTC），继承自 :class:`DomainEvent`
        session_id: 被吊销会话的 UUID
        user_id: 所属用户 UUID
        reason: 吊销原因
    """

    code: ClassVar[str] = "auth.session.revoked"

    session_id: UUID
    user_id: UUID
    reason: str
