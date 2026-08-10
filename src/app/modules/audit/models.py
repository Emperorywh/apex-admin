"""审计与登录日志领域实体 — SPEC 18.1 / 18.2 / 5.7.

领域实体是不可变 ``frozen dataclass``，不依赖 FastAPI、ORM 或任何基础设施类型
（SPEC 5.2: "领域规则不得依赖 FastAPI、ORM、HTTP 或具体存储 SDK"）。

DTO、领域对象和 ORM 模型职责分离（SPEC 5.2）。本模块定义领域对象，
``orm.py`` 定义 ORM 模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


@dataclass(frozen=True)
class DiffField:
    """单字段变更差异 — SPEC 18.2.

    每个差异字段记录变更前后的值。字段名已通过白名单校验，
    敏感字段名对应的值已被掩码（SPEC 18.2: "密码、Token、密钥等
    敏感字段不得进入差异内容"）。

    属性:
        field_name: 字段名（已通过白名单校验）。
        old_value: 变更前的值（标量或 None）。
        new_value: 变更后的值（标量或 None）。
    """

    field_name: str
    old_value: Any | None
    new_value: Any | None


@dataclass(frozen=True)
class ChangeDiff:
    """变更差异集合 — 由字段白名单生成（SPEC 18.2）.

    SPEC 18.2: "审计差异使用字段白名单生成，禁止对任意对象执行
    反射式全字段序列化"。

    此对象只包含通过白名单校验的字段差异，未声明的字段不会出现。

    属性:
        fields: 差异字段元组（已排序，保证可复现性）。
    """

    fields: tuple[DiffField, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """序列化为字典 — 供 ORM JSONB 列存储.

        返回结构::

            {
                "field_name": {"old": old_value, "new": new_value},
                ...
            }
        """

        return {
            f.field_name: {"old": f.old_value, "new": f.new_value} for f in self.fields
        }

    @property
    def is_empty(self) -> bool:
        """差异是否为空。"""

        return len(self.fields) == 0


@dataclass(frozen=True)
class AuditEntry:
    """操作审计条目 — SPEC 18.2.

    SPEC 18.2:
      - 记录操作者身份、操作时间、模块和动作。
      - 记录目标资源类型和标识。
      - 记录操作结果和 Request ID。
      - 对关键变更记录变更前后差异（字段白名单生成）。
      - 操作者/目标显示名称按操作发生时快照保存。

    成功操作的审计记录由 Use Case 显式调用 ``AuditPort`` 并与业务事务
    共同提交（SPEC 5.7: "成功操作的核心审计必须由 Use Case 显式调用
    审计 Port，并与业务事务共同提交"）。

    属性:
        id:                    全局唯一标识（UUID）。
        actor_id:              操作者标识（未认证时为 None）。
        actor_display_name:    操作者显示名快照（SPEC 18.2）。
        module:                操作所属模块编码。
        action:                审计动作编码。
        resource_type:         目标资源类型。
        resource_id:           目标资源标识（可为 None）。
        resource_display_name: 目标显示名快照（SPEC 18.2，可为 None）。
        result:                操作结果（如 ``"success"``）。
        request_id:            请求标识（可为 None）。
        diff:                  变更差异（可为 None）。
        occurred_at:           操作发生时间（UTC，带时区）。
    """

    id: UUID
    actor_id: str | None
    actor_display_name: str
    module: str
    action: str
    resource_type: str
    resource_id: str | None
    resource_display_name: str | None
    result: str
    request_id: str | None
    diff: ChangeDiff | None
    occurred_at: datetime


@dataclass(frozen=True)
class LoginLogEntry:
    """登录日志条目 — SPEC 18.1.

    SPEC 18.1: 记录用户、会话、IP、User-Agent、时间和结果。
    不记录明文密码和完整 Token（SPEC 18.1 / 12.4）。

    属性:
        id:             全局唯一标识（UUID）。
        user_id:        用户标识（登录失败时可能为 None）。
        username:       登录账号。
        session_id:     会话标识（可为 None）。
        ip_address:     客户端 IP 地址。
        user_agent:     User-Agent（可为 None）。
        result:         登录结果（``"success"`` | ``"failure"`` |
                        ``"logout"`` | ``"token_refresh_error"`` |
                        ``"force_logout"``）。
        failure_reason: 失败原因分类（成功时为 None）。
        occurred_at:    发生时间（UTC，带时区）。
    """

    id: UUID
    user_id: str | None
    username: str
    session_id: str | None
    ip_address: str
    user_agent: str | None
    result: str
    failure_reason: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class SecurityEvent:
    """失败操作安全事件 — SPEC 5.7.

    SPEC 5.7: "失败操作记录到独立安全日志，不得尝试写入已经回滚的
    业务事务"。

    安全事件通过独立安全日志渠道记录，不参与业务事务，不受业务事务
    回滚影响。

    SPEC 12.4 / 18.1: "不在日志中记录明文密码、完整 Token"。
    此模型本身不包含密码和 Token 字段，从源头杜绝泄露。

    属性:
        event_type:      事件类型分类（如 ``"auth_failure"``）。
        actor_id:        操作者标识（可为 None）。
        module:          操作所属模块编码。
        action:          操作动作编码。
        resource_type:   目标资源类型（可为 None）。
        resource_id:     目标资源标识（可为 None）。
        request_id:      请求标识（可为 None）。
        ip_address:      客户端 IP 地址（可为 None）。
        failure_reason:  失败原因描述。
        occurred_at:     发生时间（UTC，带时区）。
    """

    event_type: str
    actor_id: str | None
    module: str
    action: str
    resource_type: str | None
    resource_id: str | None
    request_id: str | None
    ip_address: str | None
    failure_reason: str
    occurred_at: datetime
