"""用户领域事件（SPEC §5.7、§11.1）。

领域事件是不依赖 FastAPI、ORM 和基础设施的不可变对象。
跨模块事件载荷只允许稳定编码、标量值和资源 ID（SPEC §5.7）。

用户模块发布 ``user.created`` 和 ``user.disabled`` 事件，
供其他模块（如认证模块的会话失效逻辑）在事务内响应。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.events.base import DomainEvent


@dataclass(frozen=True)
class UserCreated(DomainEvent):
    """用户创建事件。

    在创建用户成功后由 Use Case 发布，事务内事件处理器在
    Unit of Work 提交前同步执行（SPEC §5.7）。

    Attributes:
        occurred_at: 事件发生时间（UTC），继承自 :class:`DomainEvent`
        user_id: 被创建用户的 UUID
        username: 被创建用户的用户名
    """

    code: ClassVar[str] = "user.created"

    user_id: UUID
    username: str


@dataclass(frozen=True)
class UserDisabled(DomainEvent):
    """用户禁用事件。

    在禁用用户成功后由 Use Case 发布。认证模块（TASK-015）可监听
    此事件在事务内或事务后吊销该用户的全部会话（SPEC §12.3）。

    Attributes:
        occurred_at: 事件发生时间（UTC），继承自 :class:`DomainEvent`
        user_id: 被禁用用户的 UUID
    """

    code: ClassVar[str] = "user.disabled"

    user_id: UUID
