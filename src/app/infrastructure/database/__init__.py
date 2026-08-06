"""数据库基础设施 — SQLAlchemy 异步数据访问（SPEC §8.1）。

提供 PostgreSQL 异步连接池、Unit of Work 适配器和数据库异常映射。
所有 SQLAlchemy 类型（``AsyncEngine``、``AsyncSession``）仅出现在本层，
不向 Application 或 API 层暴露（SPEC §5.2、§8.1）。

公开接口：
- :class:`~app.infrastructure.database.engine.create_engine`：创建异步引擎
- :class:`~app.infrastructure.database.unit_of_work.SqlAlchemyUnitOfWork`：工作单元实现
- :class:`~app.infrastructure.database.db_pool_provider.SqlAlchemyDbPoolProvider`：连接池管理器
- :func:`~app.infrastructure.database.exceptions.translate_db_exception`：异常映射
"""

from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider
from app.infrastructure.database.engine import create_engine
from app.infrastructure.database.exceptions import translate_db_exception
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork

__all__ = [
    "SqlAlchemyDbPoolProvider",
    "SqlAlchemyUnitOfWork",
    "create_engine",
    "translate_db_exception",
]
