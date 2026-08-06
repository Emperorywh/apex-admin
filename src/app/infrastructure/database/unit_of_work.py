"""SqlAlchemyUnitOfWork — 异步工作单元实现（SPEC §5.6、§8.1）。

Infrastructure 层适配器，实现 Application 层的
:class:`~app.ports.unit_of_work.UnitOfWork` 端口。

职责：
- 在 ``__aenter__`` 时创建独立的 ``AsyncSession``
- 向 Repository 适配器提供会话（通过 ``session`` 属性）
- 在 ``__aexit__`` 时提交（无异常）或回滚（有异常），并关闭会话
- 在提交和退出时将数据库异常映射为稳定应用异常

关键约束（SPEC §5.6）：
- 同一 ``AsyncSession`` 不得在 ``asyncio.gather`` 并发任务间共享
- 禁止在 UoW 生命周期之外复用数据库会话
- Router 不得访问此实现或 ``AsyncSession``
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.infrastructure.database.exceptions import translate_db_exception
from app.ports.unit_of_work import UnitOfWork

_logger = logging.getLogger("app.infrastructure.database.unit_of_work")


class SqlAlchemyUnitOfWork(UnitOfWork):
    """SQLAlchemy 异步工作单元实现（SPEC §5.6、§8.1）。

    每个实例在进入 ``async with`` 上下文时创建一个独立的 ``AsyncSession``，
    退出时提交或回滚并关闭会话。Repository 适配器通过 ``session`` 属性
    获取当前会话执行数据访问。

    并发安全（SPEC §5.6）：
    - 每个 UoW 实例拥有独立的 ``AsyncSession``
    - 不得在 ``asyncio.gather`` 并发任务间共享同一个 UoW 或其会话
    - 并发任务必须分别创建各自的 UoW

    会话生命周期：
    - ``__aenter__`` 创建会话
    - ``__aexit__`` 提交/回滚后关闭会话
    - 会话关闭后不可再访问（``session`` 属性抛出 ``RuntimeError``）

    异常映射（SPEC §8.1、§10.1）：
    - 提交阶段的数据库异常在 ``_commit_internal`` 中映射
    - 上下文体内传播的数据库异常在 ``__aexit__`` 异常路径中映射
    - 已映射的应用异常不会被二次映射

    使用方式::

        async with SqlAlchemyUnitOfWork(engine) as uow:
            repo = SomeRepository(uow.session)
            repo.add(entity)
            # 退出时自动提交

    Args:
        engine: SQLAlchemy 异步引擎，用于创建会话
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        """进入事务作用域，创建独立的 AsyncSession。

        每次进入创建新的会话和事务。``expire_on_commit=False`` 避免
        异步上下文中提交后访问过期对象导致的隐式同步加载。
        """
        session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )
        self._session = session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """退出事务作用域，提交或回滚后关闭会话。

        - 无异常时：提交事务，将数据库异常映射为应用异常
        - 有异常时：回滚事务，并将原始数据库异常映射为应用异常
        - 无论结果如何都关闭会话，释放连接回连接池
        """
        if self._session is None:
            return

        # 异常路径中需要抛出的映射后异常（None 表示不需要替换原始异常）
        mapped_to_raise: Exception | None = None

        try:
            if exc_type is not None:
                # 有异常 → 回滚
                await self._session.rollback()
                # 将原始数据库异常映射为应用异常
                # （Repository 操作中的 IntegrityError / OperationalError
                #   在此处统一映射，已映射的 AppError 不再二次处理）
                if exc_val is not None and isinstance(exc_val, Exception):
                    mapped = translate_db_exception(exc_val)
                    if mapped is not exc_val:
                        mapped_to_raise = mapped
            else:
                # 无异常 → 提交（_commit_internal 内含异常映射）
                await self._commit_internal()
        finally:
            await self._session.close()
            self._session = None

        # 回滚成功后抛出映射后的应用异常（替换原始异常）
        if mapped_to_raise is not None:
            raise mapped_to_raise from exc_val

    async def commit(self) -> None:
        """显式提交当前事务（SPEC §5.6）。

        供 Use Case 在需要时显式提交。数据库约束冲突在此处映射为
        :class:`~app.errors.IntegrityConstraintError`。
        """
        self._ensure_active()
        assert self._session is not None  # narrowed by _ensure_active
        await self._commit_internal()

    async def rollback(self) -> None:
        """显式回滚当前事务（SPEC §5.6）。

        供 Use Case 在业务校验失败后显式放弃已暂存的变更。
        """
        self._ensure_active()
        assert self._session is not None  # narrowed by _ensure_active
        await self._session.rollback()

    @property
    def session(self) -> AsyncSession:
        """当前工作单元的 AsyncSession。

        Repository 适配器通过此属性获取会话执行数据访问。
        会话在 ``__aenter__`` 时创建，在 ``__aexit__`` 时关闭。
        在 UoW 作用域外访问抛出 ``RuntimeError``（SPEC §8.1：
        禁止在 UoW 生命周期之外复用数据库会话）。
        """
        self._ensure_active()
        assert self._session is not None  # narrowed by _ensure_active
        return self._session

    def _ensure_active(self) -> None:
        """验证 UoW 处于激活状态（会话已创建且未关闭）。"""
        if self._session is None:
            raise RuntimeError("工作单元未激活：请在 'async with' 上下文中使用")

    async def _commit_internal(self) -> None:
        """执行提交并将数据库异常映射为应用异常。

        提交失败时尝试回滚已暂存的变更，然后抛出映射后的应用异常。
        """
        assert self._session is not None
        try:
            await self._session.commit()
        except Exception as exc:
            # 提交失败时确保事务已回滚（SQLAlchemy 通常已自动回滚，
            # 此处显式调用作为防御性措施）
            try:
                await self._session.rollback()
            except Exception:
                _logger.warning("提交失败后的回滚也发生异常", exc_info=True)
            mapped = translate_db_exception(exc)
            if mapped is not exc:
                raise mapped from exc
            raise
