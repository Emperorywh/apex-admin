"""审计与安全日志 Port — SPEC 18.1 / 18.2 / 5.7.

SPEC 5.7:
  - 成功操作的核心审计必须由 Use Case 显式调用审计 Port，
    并与业务事务共同提交，不得依赖隐式装饰器或请求中间件猜测业务差异。
  - 失败操作记录到独立安全日志，不得尝试写入已经回滚的业务事务。

Port 定义在 Application 层（模块内部），不依赖 SQLAlchemy 或任何 ORM 类型
（SPEC 5.2: "Repository、Unit of Work、文件存储和外部服务 Port
由 Application 或 Domain 内层定义"）。Infrastructure 层的 Adapter 实现此 Port。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.audit.models import AuditEntry, LoginLogEntry, SecurityEvent


class AuditPort(ABC):
    """操作审计 Port — 成功操作审计（SPEC 18.2 / 5.7）.

    SPEC 5.7: "成功操作的核心审计必须由 Use Case 显式调用审计 Port，
    并与业务事务共同提交，不得依赖隐式装饰器或请求中间件猜测业务差异"。

    Use Case 在业务操作成功后调用此 Port，审计记录与业务数据在同一个
    Unit of Work 事务中提交。业务事务回滚时，审计记录一并回滚
    （SPEC 5.7: 同提交、同回滚）。

    Port 实现接收当前 UoW 拥有的 AsyncSession（通过 Adapter 构造注入），
    不自行提交或回滚事务（SPEC 5.6: "被调用模块的公开 Application Port
    不得提交、回滚或开启隐藏事务"）。

    SPEC 8.3 / 18.2: 审计日志不可变。此 Port 的写入方法仅提供 INSERT
    （``record_audit``），不提供 UPDATE 或 DELETE 方法。
    ``count_by_resource`` 为只读查询方法，不修改审计数据，不违反不可变约束。

    审计查询能力（``count_by_resource``）支持 SPEC 11.3 删除策略:
    已产生审计记录的用户物理删除被拒绝——调用方通过此方法检查
    资源是否已有审计记录，以决定是否允许物理删除。
    """

    @abstractmethod
    async def record_audit(self, entry: AuditEntry) -> None:
        """记录操作审计条目到当前事务.

        SPEC 18.2 / 5.7: 审计记录与业务数据在同一事务提交。
        审计记录包含操作者/目标显示名快照（SPEC 18.2）。

        参数:
            entry: 操作审计条目（不可变，含显示名快照和变更差异）。
        """

    @abstractmethod
    async def count_by_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> int:
        """查询指定资源的审计记录数量 — 只读，不修改审计数据.

        SPEC 11.3 删除策略支持: 调用方通过此方法检查资源是否已有审计记录，
        以决定是否允许物理删除（SPEC 11.3: "已产生审计记录的用户不得因物理
        删除导致审计信息失真"）。

        参数:
            resource_type: 目标资源类型（如 ``"user"``）。
            resource_id:   目标资源标识。

        返回:
            匹配的审计记录数量。
        """


class LoginLogPort(ABC):
    """登录日志 Port — SPEC 18.1.

    记录登录成功、失败、退出、Token 刷新异常和管理员强制下线。
    登录日志与触发操作的业务事务在同一 Unit of Work 提交
    （SPEC 5.7: 同事务提交）。

    SPEC 18.1: "不记录明文密码和完整 Token"。
    ``LoginLogEntry`` 不包含密码和 Token 字段。

    SPEC 8.3: 登录日志不可变。此 Port 仅提供写入方法，
    不提供 UPDATE 或 DELETE 方法。
    """

    @abstractmethod
    async def record_login(self, entry: LoginLogEntry) -> None:
        """记录登录日志到当前事务.

        SPEC 18.1: 记录用户、会话、IP、User-Agent、时间和结果。

        参数:
            entry: 登录日志条目（不可变，不含密码和 Token）。
        """


class SecurityLogPort(ABC):
    """安全日志 Port — 失败操作独立渠道（SPEC 5.7）.

    SPEC 5.7: "失败操作记录到独立安全日志，不得尝试写入已经回滚的
    业务事务"。

    此 Port 将失败操作安全事件写入独立日志渠道（structlog），
    不参与业务事务，不受业务事务回滚影响。

    SPEC 12.4 / 18.1: "不在日志中记录明文密码、完整 Token"。
    ``SecurityEvent`` 不包含密码和 Token 字段。
    """

    @abstractmethod
    def log_security_event(self, event: SecurityEvent) -> None:
        """记录失败操作到独立安全日志渠道.

        SPEC 5.7: 独立于业务事务，不受回滚影响。

        参数:
            event: 安全事件（不可变，不含密码和 Token）。
        """
