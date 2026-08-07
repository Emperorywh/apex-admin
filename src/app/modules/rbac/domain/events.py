"""RBAC 模块领域事件（SPEC §5.7、§13）。

领域事件是不依赖 FastAPI、ORM 和基础设施的不可变对象。
跨模块事件载荷只允许稳定编码、标量值和资源 ID（SPEC §5.7）。

RBAC 模块发布角色和用户-角色关系变更事件，供审计模块（TASK-019）
在事务内响应。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.events.base import DomainEvent


@dataclass(frozen=True)
class RoleCreated(DomainEvent):
    """角色创建事件。

    Attributes:
        occurred_at: 事件发生时间（UTC），继承自 :class:`DomainEvent`
        role_id: 新建角色的 UUID
        role_code: 角色编码
        is_super_admin: 是否为超级管理员角色
    """

    code: ClassVar[str] = "rbac.role.created"

    role_id: UUID
    role_code: str
    is_super_admin: bool


@dataclass(frozen=True)
class RoleDisabled(DomainEvent):
    """角色禁用事件。

    Attributes:
        occurred_at: 事件发生时间（UTC）
        role_id: 被禁用角色的 UUID
        role_code: 角色编码
    """

    code: ClassVar[str] = "rbac.role.disabled"

    role_id: UUID
    role_code: str


@dataclass(frozen=True)
class UserRoleAssigned(DomainEvent):
    """用户角色分配事件。

    Attributes:
        occurred_at: 事件发生时间（UTC）
        user_id: 被分配角色的用户 UUID
        role_id: 角色 UUID
        role_code: 角色编码
    """

    code: ClassVar[str] = "rbac.user_role.assigned"

    user_id: UUID
    role_id: UUID
    role_code: str


@dataclass(frozen=True)
class UserRoleRemoved(DomainEvent):
    """用户角色移除事件。

    Attributes:
        occurred_at: 事件发生时间（UTC）
        user_id: 被移除角色的用户 UUID
        role_id: 角色 UUID
        role_code: 角色编码
    """

    code: ClassVar[str] = "rbac.user_role.removed"

    user_id: UUID
    role_id: UUID
    role_code: str
