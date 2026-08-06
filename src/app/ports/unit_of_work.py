"""Unit of Work Port — 事务管理抽象（SPEC §5.6、§8.1）。

工作单元（Unit of Work）是 Application 层定义的事务管理端口。
每个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession
（SPEC §5.6）。Use Case 在 ``async with`` 上下文中打开 UoW，
在作用域内通过 Repository 适配器执行数据访问，退出时由 UoW
统一提交或回滚。

依赖方向：
- Application 层依赖此 Port，不依赖具体的 SQLAlchemy 实现
- Infrastructure 层（SqlAlchemyUnitOfWork）实现此 Port
- Composition Root 负责将实现注入到 Use Case

关键约束：
- Router 只能获得 Use Case，不得获得 UoW、AsyncSession 或提交接口（SPEC §5.6）
- 被调用模块的公开 Application Port 不得提交、回滚或开启隐藏事务（SPEC §5.6）
- 同一 AsyncSession 不得在并发协程任务间共享（SPEC §5.6）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self


class UnitOfWork(ABC):
    """工作单元端口（SPEC §5.6、§8.1）。

    定义事务生命周期管理的抽象接口。Use Case 通过 ``async with uow:``
    打开事务作用域，在作用域内通过 Repository 执行数据操作，
    退出时由 ``__aexit__`` 统一提交（无异常）或回滚（有异常）。

    Infrastructure 层的 ``SqlAlchemyUnitOfWork`` 实现此端口。
    Application 层只依赖此抽象接口，不依赖具体技术实现。
    """

    @abstractmethod
    async def __aenter__(self) -> Self:
        """打开事务作用域，创建数据库会话。

        Use Case 在 ``async with`` 上下文中调用。返回自身以便
        Use Case 通过返回值访问 Repository 适配器。
        """

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """关闭事务作用域，提交或回滚。

        无异常时提交事务，有异常时回滚。无论提交成功或失败，
        都关闭并释放数据库会话。数据库异常在此处映射为稳定应用异常
        （SPEC §8.1）。
        """

    @abstractmethod
    async def commit(self) -> None:
        """显式提交当前事务。

        供 Use Case 在需要时显式提交（例如多步骤事务中确认阶段性成果）。
        数据库约束冲突在此处映射为 :class:`~app.errors.IntegrityConstraintError`
        （SPEC §8.1、§10.1）。
        """

    @abstractmethod
    async def rollback(self) -> None:
        """显式回滚当前事务。

        供 Use Case 在需要时显式回滚（例如业务校验失败后放弃已暂存的变更）。
        """
