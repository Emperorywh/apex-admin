"""审计端口与共享审计类型（SPEC §5.7、§18.2）。

跨模块端口，允许其他模块的 Use Case 在同一事务内显式记录审计条目。

SPEC §5.7 要求：成功操作的核心审计必须由 Use Case 显式调用审计 Port，
并与业务事务共同提交，不得依赖隐式装饰器或请求中间件猜测业务差异。

此端口在当前 :class:`~app.ports.unit_of_work.UnitOfWork` 的事务作用域内
执行，确保审计记录与业务数据在同一事务提交
（SPEC §18.2：成功操作的审计记录按 §5.7 与业务数据在同一事务提交）。

端口层定义共享审计值类型（:class:`AuditResult`、:class:`AuditDiff`、
:class:`FieldChange`），审计领域层向下导入这些类型——端口层位于
``app.ports`` 层，领域层位于 ``app.modules`` 层，依赖方向为高层→低层，
符合分层架构约束（SPEC §5.2）。
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.ports.unit_of_work import UnitOfWork


class AuditResult(enum.StrEnum):
    """操作审计结果枚举（SPEC §18.2、§8.3）。

    Attributes:
        SUCCESS: 操作成功
        FAILED: 操作失败
    """

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class FieldChange:
    """单个字段变更记录。

    Attributes:
        field: 字段名称（白名单中且非敏感）
        old: 变更前的值（序列化为字符串）
        new: 变更后的值（序列化为字符串）
    """

    field: str
    old: str | None
    new: str | None


@dataclass(frozen=True)
class AuditDiff:
    """审计变更差异（SPEC §18.2）。

    不可变对象，包含 :class:`FieldChange` 列表。仅包含白名单中实际
    发生变化的字段，敏感字段被过滤。

    Attributes:
        changes: 字段变更列表（已过滤敏感字段）
    """

    changes: tuple[FieldChange, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """差异是否为空（无变更字段）。"""
        return len(self.changes) == 0


class AuditPort(ABC):
    """审计端口（SPEC §5.7、§18.2）。

    由审计模块实现，供其他模块的 Use Case 在同一事务内显式调用。
    审计记录与业务数据在同一事务提交（SPEC §18.2）。

    此端口不得提交、回滚或开启隐藏事务（SPEC §5.6）。
    所有操作在传入 UoW 的事务作用域内执行。
    """

    @abstractmethod
    async def record(  # noqa: PLR0913
        self,
        uow: UnitOfWork,
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
    ) -> None:
        """在当前事务内记录操作审计（SPEC §5.7、§18.2）。

        审计记录写入传入 UoW 的 ``AsyncSession``，与业务数据在同一事务
        中提交或回滚。Use Case 显式调用此方法记录核心操作
        （SPEC §5.7：不得依赖隐式装饰器或请求中间件）。

        Args:
            uow: 当前 Unit of Work（审计记录在同一事务提交）
            actor_id: 操作者 ID；未认证操作为 None
            actor_display_name: 操作者显示名称快照（操作发生时）
            occurred_at: 操作时间（UTC）
            module: 操作模块编码
            action: 操作动作编码
            resource_type: 目标资源类型编码
            resource_id: 目标资源标识
            resource_display_name: 目标显示名称快照（操作发生时）
            result: 操作结果
            request_id: 请求 ID
            diff: 变更差异（白名单生成，已过滤敏感字段）
        """
