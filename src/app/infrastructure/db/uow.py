"""SqlAlchemyUnitOfWork — SPEC 5.6 事务工作单元基础设施实现.

SPEC 5.6 关键约束:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
  - Repository Adapter 由 Composition Root 使用当前 Unit of Work
    拥有的 AsyncSession 构造。
  - 最外层写 Use Case 负责开始、提交或回滚。
  - 禁止通过 ContextVar、线程局部变量或全局变量隐式获取数据库会话。
  - 同一 AsyncSession 不得在并发协程任务间共享。
  - 除显式 Savepoint 外禁止嵌套事务。

生命周期:
  1. ``__aenter__``: 创建 ``AsyncSession`` 并开始事务。
  2. Use Case 执行业务操作。
  3. ``commit()``: Use Case 显式提交；SQLAlchemy 异常被翻译为应用异常。
  4. ``__aexit__``: 异常时自动回滚；始终关闭会话使其不可用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from app.application.ports import UnitOfWork
from app.infrastructure.db.exceptions import translate_db_exception

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


class SqlAlchemyUnitOfWork(UnitOfWork):
    """SQLAlchemy 异步事务工作单元 — SPEC 5.6 唯一实现.

    每个实例拥有独占的 ``AsyncSession``，上下文退出后即关闭，
    不可跨 Use Case 或并发协程复用（SPEC 5.6）。

    ``session`` 属性供 Composition Root 构造 Repository Adapter 使用，
    不通过 Port 暴露，确保 Application 层不感知 SQLAlchemy 类型。
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """初始化 UoW，绑定异步引擎。

        参数:
            engine: 共享的 ``AsyncEngine`` 实例（由 Composition Root 创建）。
        """

        self._engine: AsyncEngine = engine
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        """返回当前活跃的 ``AsyncSession``.

        供 Composition Root 构造 Repository Adapter。
        UoW 未激活（未进入上下文或已退出）时抛出 ``RuntimeError``。

        SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前
        Unit of Work 拥有的 AsyncSession 构造"。
        """

        if self._session is None:
            raise RuntimeError(
                "UnitOfWork 未激活：请在 'async with' 上下文内访问 session",
            )
        return self._session

    async def __aenter__(self) -> Self:
        """创建并绑定 ``AsyncSession``，开始事务上下文。"""

        from sqlalchemy.ext.asyncio import AsyncSession

        # expire_on_commit=False 使得提交后 ORM 对象仍可访问属性，
        # 避免在 Use Case 提交后读取返回值时触发隐式 lazy load。
        self._session = AsyncSession(self._engine, expire_on_commit=False)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """结束事务上下文.

        - 有异常时自动回滚（SPEC 5.6: 异常完整回滚）。
        - 无论成功或异常，始终关闭 ``AsyncSession`` 使其不可用
          （SPEC 8.1: "禁止在 Unit of Work 生命周期之外复用数据库会话"）。
        """

        assert self._session is not None

        try:
            if exc_type is not None:
                await self._rollback_internal()
        finally:
            await self._session.close()
            # 置空确保退出后 session 不可用
            self._session = None

    async def commit(self) -> None:
        """显式提交当前事务.

        SPEC 5.6: 提交由最外层写 Use Case 调用。
        SQLAlchemy 异常翻译为稳定应用异常，不泄漏 ORM 类型。
        """

        if self._session is None:
            raise RuntimeError(
                "UnitOfWork 未激活：请在 'async with' 上下文内调用 commit",
            )

        from sqlalchemy.exc import SQLAlchemyError

        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            raise translate_db_exception(exc) from exc

    async def rollback(self) -> None:
        """显式回滚当前事务."""

        if self._session is None:
            raise RuntimeError(
                "UnitOfWork 未激活：请在 'async with' 上下文内调用 rollback",
            )

        await self._rollback_internal()

    async def _rollback_internal(self) -> None:
        """内部回滚 — 不检查 session 是否为 None，由调用方保证."""

        assert self._session is not None

        from sqlalchemy.exc import SQLAlchemyError

        try:
            await self._session.rollback()
        except SQLAlchemyError as exc:
            # 回滚本身失败翻译为应用异常，但不应阻塞 finally 中的 close
            raise translate_db_exception(exc) from exc
