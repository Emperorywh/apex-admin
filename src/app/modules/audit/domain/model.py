"""审计领域实体与枚举（SPEC §18.1–18.2）。

包含操作审计实体和登录日志实体，以及登录结果枚举。

操作审计（:class:`AuditLog`）记录操作者身份、时间、模块、动作、目标资源、
结果、Request ID 和变更差异（SPEC §18.2）。

登录日志（:class:`LoginLog`）记录用户、会话、IP、User-Agent、时间和结果
（SPEC §18.1）。

实体不可变（frozen dataclass），通过工厂方法创建。变更差异以
:class:`~app.ports.audit.AuditDiff` 不可变对象存储。

操作审计结果枚举（:class:`~app.ports.audit.AuditResult`）和差异值类型
（:class:`~app.ports.audit.AuditDiff`、:class:`~app.ports.audit.FieldChange`）
定义在端口层（``app.ports.audit``），此处导入——端口层位于 ``app.ports``
层，领域层位于 ``app.modules`` 层，依赖方向为高层→低层。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.ports.audit import AuditDiff, AuditResult


class LoginResult(enum.StrEnum):
    """登录日志结果枚举（SPEC §18.1、§8.3）。

    Attributes:
        LOGIN_SUCCESS: 登录成功
        LOGIN_FAILED: 登录失败
        LOGOUT: 退出登录
        TOKEN_ERROR: Token 刷新异常
        FORCED_LOGOUT: 管理员强制下线
    """

    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    TOKEN_ERROR = "token_error"
    FORCED_LOGOUT = "forced_logout"


@dataclass(frozen=True)
class AuditLog:
    """操作审计实体（SPEC §18.2）。

    记录一次操作的完整审计信息。操作者显示名称和目标显示名称按操作
    发生时的快照保存（SPEC §18.2：易变信息按操作发生时快照保存）。

    变更差异（``diff``）通过字段白名单生成，敏感字段永不进入差异
    （SPEC §18.2：密码、Token、密钥等敏感字段不得进入差异内容）。

    Attributes:
        id: 审计记录 UUID（自动生成）
        actor_id: 操作者 ID（未认证操作为 None）
        actor_display_name: 操作者显示名称快照（操作发生时）
        occurred_at: 操作时间（UTC）
        module: 操作模块编码（如 ``user``、``auth``、``rbac``）
        action: 操作动作编码（如 ``user.create``、``user.status.change``）
        resource_type: 目标资源类型编码（如 ``user:user``）
        resource_id: 目标资源标识（可空，如列表查询无特定目标）
        resource_display_name: 目标显示名称快照（操作发生时）
        result: 操作结果（成功 / 失败）
        request_id: 请求 ID（用于跨日志关联和审计追踪）
        diff: 变更差异（前后值），无变更时为 None
    """

    id: UUID
    actor_id: UUID | None
    actor_display_name: str | None
    occurred_at: datetime
    module: str
    action: str
    resource_type: str | None
    resource_id: str | None
    resource_display_name: str | None
    result: AuditResult
    request_id: str | None
    diff: AuditDiff | None = None

    @classmethod
    def new(  # noqa: PLR0913
        cls,
        *,
        actor_id: UUID | None,
        actor_display_name: str | None,
        occurred_at: datetime,
        module: str,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        resource_display_name: str | None = None,
        result: AuditResult,
        request_id: str | None = None,
        diff: AuditDiff | None = None,
    ) -> AuditLog:
        """创建操作审计实体。

        Args:
            actor_id: 操作者 ID；未认证操作为 None
            actor_display_name: 操作者显示名称快照
            occurred_at: 操作时间（UTC）
            module: 操作模块编码
            action: 操作动作编码
            resource_type: 目标资源类型编码
            resource_id: 目标资源标识
            resource_display_name: 目标显示名称快照
            result: 操作结果
            request_id: 请求 ID
            diff: 变更差异

        Returns:
            新创建的 :class:`AuditLog`
        """
        return cls(
            id=uuid4(),
            actor_id=actor_id,
            actor_display_name=actor_display_name,
            occurred_at=occurred_at,
            module=module,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result=result,
            request_id=request_id,
            diff=diff,
        )


@dataclass(frozen=True)
class LoginLog:
    """登录日志实体（SPEC §18.1）。

    记录登录成功、登录失败、退出登录、Token 刷新异常和管理员强制下线
    （SPEC §18.1）。

    不记录明文密码和完整 Token（SPEC §18.1）。

    Attributes:
        id: 登录日志 UUID（自动生成）
        user_id: 用户 ID（用户不存在时可能为 None）
        username: 用户名（规范化前的小写形式）
        session_id: 会话 ID（无会话时为 None）
        ip: 客户端 IP
        user_agent: 客户端 User-Agent
        occurred_at: 发生时间（UTC）
        result: 登录结果
        failure_reason: 失败原因（仅失败时有值）
    """

    id: UUID
    user_id: UUID | None
    username: str | None
    session_id: UUID | None
    ip: str
    user_agent: str
    occurred_at: datetime
    result: LoginResult
    failure_reason: str | None = None

    @classmethod
    def new(  # noqa: PLR0913
        cls,
        *,
        user_id: UUID | None,
        username: str | None,
        session_id: UUID | None,
        ip: str,
        user_agent: str,
        occurred_at: datetime,
        result: LoginResult,
        failure_reason: str | None = None,
    ) -> LoginLog:
        """创建登录日志实体。

        Args:
            user_id: 用户 ID；用户不存在时为 None
            username: 用户名
            session_id: 会话 ID
            ip: 客户端 IP
            user_agent: 客户端 User-Agent
            occurred_at: 发生时间（UTC）
            result: 登录结果
            failure_reason: 失败原因

        Returns:
            新创建的 :class:`LoginLog`
        """
        return cls(
            id=uuid4(),
            user_id=user_id,
            username=username,
            session_id=session_id,
            ip=ip,
            user_agent=user_agent,
            occurred_at=occurred_at,
            result=result,
            failure_reason=failure_reason,
        )
