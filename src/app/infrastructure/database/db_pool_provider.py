"""SQLAlchemy 连接池 Provider（SPEC §6.1、§8.1）。

Infrastructure 层适配器，实现 :class:`~app.health.providers.DbPoolProvider` 端口。
管理 ``AsyncEngine`` 的生命周期：

- ``initialize()``：创建引擎和连接池
- ``dispose()``：释放连接池
- ``check_connection()``：验证数据库连通性

Composition Root 在 :func:`~app.app.create_app` 中创建此 provider 实例，
Lifespan 在启动时调用 ``initialize()``，关闭时调用 ``dispose()``。

Use Case 通过 :meth:`create_unit_of_work` 获取工作单元。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config.settings import Settings
from app.health.providers import DbPoolProvider
from app.infrastructure.database.engine import create_engine
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

_logger = logging.getLogger("app.infrastructure.database.db_pool_provider")


class SqlAlchemyDbPoolProvider(DbPoolProvider):
    """基于 SQLAlchemy AsyncEngine 的连接池管理器（SPEC §6.1、§8.1）。

    管理引擎生命周期并提供连通性检查和 UoW 工厂。
    引擎在 ``initialize()`` 时创建，在 ``dispose()`` 时释放。

    Args:
        settings: 部署配置，提供 ``database_url``、``db_pool_size`` 和 ``db_max_overflow``
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None

    @property
    def engine(self) -> AsyncEngine | None:
        """返回当前异步引擎。

        引擎在 :meth:`initialize` 时创建，在 :meth:`dispose` 时置空。
        未初始化时返回 ``None``。供同层基础设施适配器（如 Alembic revision
        校验探针）共享同一引擎，避免创建额外连接池。
        """
        return self._engine

    async def initialize(self) -> None:
        """创建异步引擎和连接池（SPEC §6.1）。

        引擎在此时惰性创建。数据库暂时不可用不影响引擎创建本身
        （连接在首次使用时才建立），只影响后续的连通性检查。
        """
        self._engine = create_engine(
            self._settings.database_url,
            pool_size=self._settings.db_pool_size,
            max_overflow=self._settings.db_max_overflow,
        )
        _logger.info(
            "数据库连接池已初始化",
            extra={
                "pool_size": self._settings.db_pool_size,
                "max_overflow": self._settings.db_max_overflow,
            },
        )

    async def dispose(self) -> None:
        """释放连接池和相关资源（SPEC §6.1）。

        确保所有连接被正确归还，引擎被正确释放。
        """
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
        _logger.info("数据库连接池已释放")

    async def check_connection(self) -> bool:
        """检查数据库连接是否可用（SPEC §6.2）。

        执行轻量级 ``SELECT 1`` 验证连通性。恢复后无需重启即可重新就绪。
        """
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            _logger.warning("数据库连通性检查失败", exc_info=True)
            return False

    def create_unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """创建一个新的工作单元（SPEC §5.6）。

        每次 Use Case 执行时调用，返回拥有独立 AsyncSession 的 UoW。
        并发任务必须各自调用此方法获取独立的 UoW，不得共享（SPEC §5.6）。

        Returns:
            新的 :class:`~app.infrastructure.database.unit_of_work.SqlAlchemyUnitOfWork`

        Raises:
            RuntimeError: 引擎未初始化（未调用 ``initialize()`` 或已 ``dispose()``）
        """
        if self._engine is None:
            raise RuntimeError("数据库连接池未初始化：请先调用 initialize()")
        return SqlAlchemyUnitOfWork(self._engine)
