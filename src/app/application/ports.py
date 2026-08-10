"""显式 Port — Clock、ID Generator、UnitOfWork 与健康检查（SPEC 5.6 / 5.8）.

领域规则通过显式 Clock Port 获取时间，通过显式 ID Generator Port
获取标识。Port 定义在 Application 层，具体实现可由 Composition Root
注入（SPEC 5.2: Port 由 Application 或 Domain 内层定义）。

UnitOfWork Port（SPEC 5.6）定义在 Application 层，Infrastructure
使用 SqlAlchemyUnitOfWork 实现。Port 不暴露 AsyncSession 或任何
SQLAlchemy 类型，确保内层不依赖具体 ORM。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from types import TracebackType


class Clock(ABC):
    """时钟 Port — 领域规则通过此 Port 获取当前时间.

    SPEC 5.8: "领域规则通过显式 Clock Port 获取时间"。
    使用 Port 而非直接调用 ``datetime.now()`` 使得时间来源可控，
    便于测试中注入固定时间。
    """

    @abstractmethod
    def now(self) -> datetime:
        """返回当前 UTC 时间."""


class SystemClock(Clock):
    """系统时钟实现 — 基于系统时钟返回 UTC 时间.

    这是生产环境的默认实现。测试中可替换为返回固定时间的伪实现。
    SPEC 6.3: 所有时间统一使用 UTC，返回带时区的 datetime。
    """

    def now(self) -> datetime:
        """返回当前系统 UTC 时间（带时区）."""

        return datetime.now(UTC)


class IdGenerator(ABC):
    """标识生成器 Port — 领域规则通过此 Port 获取唯一标识.

    SPEC 5.8: "通过显式 ID Generator Port 获取标识"。
    使用 Port 而非直接调用 ``uuid4()`` 使得标识生成方式可控，
    便于测试中注入确定性标识。
    """

    @abstractmethod
    def generate_id(self) -> UUID:
        """生成并返回一个新的唯一标识."""


class UuidGenerator(IdGenerator):
    """UUID v4 标识生成器实现.

    这是生产环境的默认实现，使用 ``uuid4`` 生成密码学安全的随机 UUID。
    """

    def generate_id(self) -> UUID:
        """生成并返回一个新的 UUID v4 标识."""

        return uuid4()


# ── Unit of Work Port（SPEC 5.6）──────────────────────────────────────────


class UnitOfWork(ABC):
    """事务工作单元 Port — SPEC 5.6.

    SPEC 5.6:
      - ``UnitOfWork`` Port 定义在 Application 层，
        Infrastructure 使用 ``SqlAlchemyUnitOfWork`` 实现。
      - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
      - 最外层写 Use Case 负责开始、提交或回滚。
      - 禁止通过 ContextVar、线程局部变量或全局变量隐式获取数据库会话。

    使用方式::

        async with uow:
            # 执行业务操作（通过 Repository）
            await uow.commit()

    异常时 ``__aexit__`` 自动回滚并释放会话，保证不留半完成事务。
    """

    @abstractmethod
    async def __aenter__(self) -> Self:
        """开始事务上下文 — 创建并绑定 AsyncSession."""

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """结束事务上下文 — 异常时回滚，始终关闭并释放 AsyncSession."""

    @abstractmethod
    async def commit(self) -> None:
        """提交当前事务.

        SPEC 5.6: 提交由最外层写 Use Case 显式调用。
        底层 SQLAlchemy 异常被翻译为稳定应用异常。
        """

    @abstractmethod
    async def rollback(self) -> None:
        """回滚当前事务.

        SPEC 5.6: 可由 Use Case 显式调用，也可在异常退出时自动触发。
        """


# ── 健康检查 Port（SPEC 6.2）─────────────────────────────────────────────


@dataclass(frozen=True)
class HealthResult:
    """健康检查结果.

    SPEC 6.2:
      - ``healthy`` 标识检查是否通过。
      - ``code`` 为稳定错误码，客户端可据此判断失败类别。
      - ``detail`` 为人类可读说明，仅供展示，不作业务判断依据。
    """

    healthy: bool
    code: str
    detail: str


class HealthCheck(ABC):
    """健康检查 Port — SPEC 6.2.

    Infrastructure 层实现具体的数据库连通性与迁移版本检查。
    API 层通过此 Port 调用，不直接依赖 SQLAlchemy 或 Alembic。
    """

    @abstractmethod
    async def check_ready(self) -> HealthResult:
        """执行就绪检查 — 验证数据库可用且迁移版本一致."""
