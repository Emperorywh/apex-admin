"""用户领域事件 — SPEC 5.7.

SPEC 5.7:
  - Domain Event 是不依赖 FastAPI、ORM 和基础设施的不可变对象。
  - 跨模块事件载荷只允许稳定编码、标量值和资源 ID，
    不得携带 ORM 模型或可变领域对象。

事件用途（TASK-012 范围）:
  - ``USER.DISABLED``: 用户被禁用时发布，auth 模块（TASK-013）注册事务内
    处理器吊销该用户的全部会话。本任务只发布事件。
  - ``USER.PASSWORD_RESET_BY_ADMIN``: 管理员重置用户密码时发布，auth 模块
    （TASK-013）注册事务内处理器吊销该用户的全部会话。本任务只发布事件。

SPEC 5.7: "事件及处理器通过 ``ModuleDefinition`` 显式注册"。
处理器由 auth 模块（TASK-013）注册，本任务在 ``ModuleDefinition`` 中
声明事件编码供启动校验检测重复。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.events.events import DomainEvent


@dataclass(frozen=True)
class UserDisabled(DomainEvent):
    """用户禁用事件 — 在禁用 Use Case 的事务内分发.

    SPEC 5.7: "跨模块事件载荷只允许稳定编码、标量值和资源 ID"。
    载荷携带 user_id（UUID 字符串）和 user_status（稳定编码），均为标量值。

    auth 模块（TASK-013）注册事务内处理器监听此事件，在当前事务内
    吊销该用户的全部会话（SPEC 12.3: "用户被禁用后，其有效会话全部失效"）。

    属性:
        code:        固定为 ``USER.DISABLED``。
        user_id:     被禁用的用户 ID（UUID 字符串形式）。
        user_status: 禁用后的用户状态（固定为 ``disabled``）。
    """

    user_id: str = ""
    user_status: str = ""


@dataclass(frozen=True)
class PasswordResetByAdmin(DomainEvent):
    """管理员重置密码事件 — 在重置密码 Use Case 的事务内分发.

    SPEC 5.7: "跨模块事件载荷只允许稳定编码、标量值和资源 ID"。
    载荷仅携带 user_id（UUID 字符串），不含密码或哈希。

    auth 模块（TASK-013）注册事务内处理器监听此事件，在当前事务内
    吊销该用户的全部会话（SPEC 12.3: "管理员重置密码后吊销该用户全部会话"）。

    属性:
        code:    固定为 ``USER.PASSWORD_RESET_BY_ADMIN``。
        user_id: 被重置密码的用户 ID（UUID 字符串形式）。
    """

    user_id: str = ""
