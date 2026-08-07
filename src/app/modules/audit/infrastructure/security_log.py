"""安全事件日志（SPEC §5.7、§18.1–18.2）。

失败操作记录到独立安全日志，不得尝试写入已经回滚的业务事务
（SPEC §5.7：失败操作记录到独立安全日志）。

此模块使用结构化 Python 日志记录安全事件，与数据库事务解耦——
即使业务事务已回滚，安全事件仍被记录到日志输出
（SPEC §5.7：不得尝试写入已经回滚的业务事务）。

密码、Token 和验证码不在任何日志中（SPEC §18.2）。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger("app.audit.security")

#: 允许记录到安全日志的字段名（白名单）。
#:
#: 安全日志只记录与安全事件相关的标识和分类信息，不记录任何敏感数据。
#: 注意：``op_module`` 替代 ``module`` 以避免与 Python LogRecord 保留属性冲突。
_ALLOWED_SECURITY_LOG_FIELDS = frozenset(
    {
        "event",
        "op_module",
        "action",
        "actor_id",
        "resource_type",
        "resource_id",
        "result",
        "reason",
        "ip",
        "user_agent",
        "username",
        "user_id",
        "session_id",
        "request_id",
    }
)

#: 禁止记录到安全日志的敏感字段（SPEC §18.2、§23.2）。
_FORBIDDEN_LOG_FIELDS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "key",
        "secret",
        "authorization",
        "verification_code",
        "captcha",
        "code",
    }
)


class SecurityEventLogger:
    """安全事件日志记录器（SPEC §5.7、§18.1–18.2）。

    使用结构化 Python 日志记录安全事件（成功或失败的操作）。
    与数据库事务完全解耦——即使业务事务已回滚，安全事件仍被记录
    （SPEC §5.7）。

    安全日志用于：
    - 失败操作的审计（SPEC §5.7：不得尝试写入已经回滚的业务事务）
    - 登录安全事件（SPEC §18.1：记录登录失败、Token 刷新异常等）
    - 权限/角色/用户状态变更（SPEC §18.2）

    密码、Token 和验证码不在任何日志中（SPEC §18.2）。
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """初始化安全事件日志记录器。

        Args:
            logger: 自定义日志记录器；为 None 时使用默认审计安全日志记录器
        """
        self._logger = logger or _logger

    def log_security_event(
        self,
        *,
        event: str,
        level: int = logging.WARNING,
        **fields: object,
    ) -> None:
        """记录安全事件（SPEC §5.7、§18.1–18.2）。

        将安全事件以结构化日志记录。字段经白名单过滤，敏感字段被丢弃
        （SPEC §18.2：密码、Token 和验证码不在任何日志中）。

        Args:
            event: 安全事件编码（如 ``operation_failed``、``login_failed``）
            level: 日志级别，默认 WARNING
            **fields: 安全事件字段（仅记录白名单中且非敏感的字段）
        """
        safe_fields: dict[str, object] = {"event": event}
        for key, value in fields.items():
            if key in _FORBIDDEN_LOG_FIELDS or key not in _ALLOWED_SECURITY_LOG_FIELDS:
                continue
            safe_fields[key] = value

        self._logger.log(
            level,
            "安全事件",
            extra=safe_fields,
        )

    def log_operation_failed(
        self,
        *,
        module: str,
        action: str,
        actor_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        reason: str,
        request_id: str | None = None,
    ) -> None:
        """记录失败操作安全事件（SPEC §5.7）。

        失败操作记录到独立安全日志，不在已回滚的业务事务中
        （SPEC §5.7：不得尝试写入已经回滚的业务事务）。

        Args:
            module: 操作模块编码
            action: 操作动作编码
            actor_id: 操作者 ID
            resource_type: 目标资源类型编码
            resource_id: 目标资源标识
            reason: 失败原因
            request_id: 请求 ID
        """
        self.log_security_event(
            event="operation_failed",
            level=logging.WARNING,
            op_module=module,
            action=action,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=reason,
            request_id=request_id,
        )

    def log_login_event(
        self,
        *,
        result: str,
        username: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        reason: str | None = None,
    ) -> None:
        """记录登录安全事件（SPEC §18.1）。

        记录登录成功、失败、退出登录、Token 异常和管理员强制下线。
        不记录明文密码和完整 Token（SPEC §18.1）。

        Args:
            result: 登录结果编码（如 ``login_success``、``login_failed``）
            username: 用户名（规范化前的小写形式）
            user_id: 用户 ID
            session_id: 会话 ID
            ip: 客户端 IP
            user_agent: 客户端 User-Agent
            reason: 失败原因（仅失败时）
        """
        self.log_security_event(
            event=result,
            level=logging.INFO if "success" in result else logging.WARNING,
            username=username,
            user_id=user_id,
            session_id=session_id,
            ip=ip,
            user_agent=user_agent[:200] if user_agent else None,
            reason=reason,
        )
