"""独立安全日志渠道 — SPEC 5.7.

SPEC 5.7: "失败操作记录到独立安全日志，不得尝试写入已经回滚的
业务事务"。

此模块使用独立的 structlog logger 实例记录失败操作安全事件，
不参与业务事务，不受业务事务回滚影响。

SPEC 12.4 / 18.1: "不在日志中记录明文密码、完整 Token"。
``SecurityEvent`` 模型本身不包含密码和 Token 字段，从源头杜绝泄露。
此外 structlog 的 ``mask_sensitive_fields`` 处理器提供二次过滤
（SPEC 23.3: "日志内容防止敏感信息泄露"）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.modules.audit.port import SecurityLogPort

if TYPE_CHECKING:
    from app.modules.audit.models import SecurityEvent

# 独立 logger 名称 — 与业务日志分离。
# 运维可据此配置独立收集与保留策略（SPEC 18.4: "安全事件的保留策略
# 独立于普通访问日志"）。
_SECURITY_LOGGER_NAME = "apex.security"


class StructlogSecurityLogger(SecurityLogPort):
    """structlog 安全日志实现 — 失败操作独立渠道（SPEC 5.7）.

    使用独立 logger 名称 ``apex.security``，与业务日志分离。
    安全事件以 WARNING 级别记录（失败操作需要关注但不一定是系统错误）。

    SPEC 12.4 / 18.1: 不记录明文密码和完整 Token。
    ``SecurityEvent`` 模型不包含密码和 Token 字段，从源头杜绝。
    structlog 的 ``mask_sensitive_fields`` 处理器提供二次过滤。
    """

    def __init__(self) -> None:
        """初始化安全日志器，获取独立 logger 实例。"""

        self._logger = structlog.get_logger(_SECURITY_LOGGER_NAME)

    def log_security_event(self, event: SecurityEvent) -> None:
        """记录失败操作到独立安全日志渠道.

        SPEC 5.7: 独立于业务事务，不受回滚影响。
        事件以 WARNING 级别记录，包含事件类型、操作者、模块、动作、
        资源、请求标识、IP 和失败原因。

        参数:
            event: 安全事件（不可变，不含密码和 Token）。
        """

        self._logger.warning(
            "security_event",
            event_type=event.event_type,
            actor_id=event.actor_id,
            module=event.module,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            request_id=event.request_id,
            ip_address=event.ip_address,
            failure_reason=event.failure_reason,
            occurred_at=event.occurred_at.isoformat(),
        )
