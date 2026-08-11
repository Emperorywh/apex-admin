"""审计查询 Port — 只读查询接口（SPEC 18.3）.

SPEC 18.3: 分页查询登录日志与操作审计、按条件筛选、查看单次操作详情。

这些 Port 与写入 Port（``AuditPort`` / ``LoginLogPort``）分离:
  - 写入 Port 仅提供 INSERT（不可变约束）。
  - 查询 Port 仅提供 SELECT（只读）。
两者不互相依赖，职责清晰分离（SPEC 5.2: 单一职责）。

Port 定义在 Application 层（模块内部），不依赖 SQLAlchemy 或任何 ORM 类型
（SPEC 5.2）。Infrastructure 层的 Adapter 实现此 Port。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.modules.audit.models import AuditEntry, LoginLogEntry


@dataclass(frozen=True)
class AuditLogFilters:
    """审计日志查询筛选条件 — SPEC 18.3.

    SPEC 18.3: 按操作者、模块、动作、资源、结果和时间范围筛选。

    所有字段可选，``None`` 表示不筛选该条件。

    属性:
        actor_id:       操作者标识筛选。
        module:         操作模块筛选。
        action:         审计动作筛选。
        resource_type:  目标资源类型筛选。
        resource_id:    目标资源标识筛选。
        result:         操作结果筛选。
        start_time:     发生时间下界（含）。
        end_time:       发生时间上界（含）。
    """

    actor_id: str | None = None
    module: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    result: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


@dataclass(frozen=True)
class LoginLogFilters:
    """登录日志查询筛选条件 — SPEC 18.1 / 18.3.

    SPEC 18.1 G3: 按用户、时间、IP 和结果分页查询。

    所有字段可选，``None`` 表示不筛选该条件。

    属性:
        user_id:    用户标识筛选。
        username:   登录账号筛选。
        ip_address: 客户端 IP 筛选。
        result:     登录结果筛选。
        start_time: 发生时间下界（含）。
        end_time:   发生时间上界（含）。
    """

    user_id: str | None = None
    username: str | None = None
    ip_address: str | None = None
    result: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class AuditQueryPort(ABC):
    """审计日志只读查询 Port — SPEC 18.3.

    SPEC 8.3 / 18.2: 审计日志不可变。此 Port 的所有方法均为只读 SELECT，
    不修改审计数据。

    查询方法接收当前 UoW 拥有的 AsyncSession（通过 Adapter 构造注入），
    不自行提交或回滚事务。
    """

    @abstractmethod
    async def query_audit_logs(
        self,
        filters: AuditLogFilters,
        offset: int,
        limit: int,
    ) -> tuple[list[AuditEntry], int]:
        """分页查询审计日志 — SPEC 18.3.

        参数:
            filters: 筛选条件。
            offset:  零基偏移量。
            limit:   每页数量。

        返回:
            (审计日志条目列表, 符合条件的记录总数)。
        """

    @abstractmethod
    async def get_audit_log_by_id(
        self,
        log_id: UUID,
    ) -> AuditEntry | None:
        """按 ID 查询单条审计日志 — SPEC 18.3.

        参数:
            log_id: 审计日志 ID。

        返回:
            审计日志条目，不存在时返回 None。
        """


class LoginLogQueryPort(ABC):
    """登录日志只读查询 Port — SPEC 18.1 / 18.3.

    SPEC 8.3 / 18.1: 登录日志不可变。此 Port 的所有方法均为只读 SELECT，
    不修改审计数据。
    """

    @abstractmethod
    async def query_login_logs(
        self,
        filters: LoginLogFilters,
        offset: int,
        limit: int,
    ) -> tuple[list[LoginLogEntry], int]:
        """分页查询登录日志 — SPEC 18.1 / 18.3.

        参数:
            filters: 筛选条件。
            offset:  零基偏移量。
            limit:   每页数量。

        返回:
            (登录日志条目列表, 符合条件的记录总数)。
        """

    @abstractmethod
    async def get_login_log_by_id(
        self,
        log_id: UUID,
    ) -> LoginLogEntry | None:
        """按 ID 查询单条登录日志 — SPEC 18.3.

        参数:
            log_id: 登录日志 ID。

        返回:
            登录日志条目，不存在时返回 None。
        """


class AuditRetentionPort(ABC):
    """审计日志保留治理 Port — SPEC 18.4.

    SPEC 18.4: 提供受控的归档或清理命令。

    此 Port 提供过期记录的计数和删除操作。删除操作仅在受控的
    管理命令中调用（``audit cleanup --apply``），不存在于普通业务
    CRUD 路径中（SPEC 8.3: 审计日志不通过普通业务 CRUD 修改）。

    安全事件的保留策略独立于普通访问日志（SPEC 18.4）。
    安全事件通过 structlog 独立渠道记录，其保留/轮转由日志收集
    与轮转任务（TASK-029/031）负责，不由此 Port 管理。
    """

    @abstractmethod
    async def count_expired_audit_logs(self, cutoff: datetime) -> int:
        """统计过期的审计日志数量 — 只读.

        参数:
            cutoff: 截止时间，``occurred_at`` 早于此时间的记录视为过期。

        返回:
            过期记录数量。
        """

    @abstractmethod
    async def delete_expired_audit_logs(self, cutoff: datetime) -> int:
        """删除过期的审计日志 — 受控操作（SPEC 18.4 / 25.3）.

        仅在管理命令 ``audit cleanup --apply`` 中调用。

        参数:
            cutoff: 截止时间。

        返回:
            已删除的记录数量。
        """

    @abstractmethod
    async def count_expired_login_logs(self, cutoff: datetime) -> int:
        """统计过期的登录日志数量 — 只读.

        参数:
            cutoff: 截止时间。

        返回:
            过期记录数量。
        """

    @abstractmethod
    async def delete_expired_login_logs(self, cutoff: datetime) -> int:
        """删除过期的登录日志 — 受控操作（SPEC 18.4 / 25.3）.

        参数:
            cutoff: 截止时间。

        返回:
            已删除的记录数量。
        """
