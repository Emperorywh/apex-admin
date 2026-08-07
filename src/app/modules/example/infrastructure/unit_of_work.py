"""示例模块工作单元实现（SPEC §5.6）。

继承 :class:`~app.infrastructure.database.unit_of_work.SqlAlchemyUnitOfWork`
（提供 ``__aenter__``/``__aexit__``/``commit``/``rollback`` 和 ``session`` 属性），
同时实现 :class:`~app.modules.example.application.port.ExampleUnitOfWork`
（提供 ``examples`` Repository 访问）。

Use Case 通过 :class:`ExampleUnitOfWork` 端口使用此实现，不直接接触
``AsyncSession``（SPEC §5.6）。
"""

from __future__ import annotations

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.modules.example.application.port import ExampleUnitOfWork
from app.modules.example.infrastructure.repository import SqlAlchemyExampleRepository


class SqlAlchemyExampleUnitOfWork(SqlAlchemyUnitOfWork, ExampleUnitOfWork):
    """示例模块 SQLAlchemy 工作单元。

    在基类事务管理的基础上，通过 :attr:`examples` 属性提供
    :class:`~app.modules.example.infrastructure.repository.SqlAlchemyExampleRepository`
    访问。Repository 每次访问时从当前 Session 构造，不缓存状态。
    """

    @property
    def examples(self) -> SqlAlchemyExampleRepository:
        """当前事务作用域的示例 Repository。"""
        return SqlAlchemyExampleRepository(self.session)
