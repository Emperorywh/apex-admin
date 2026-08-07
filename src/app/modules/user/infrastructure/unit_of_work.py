"""用户模块工作单元实现（SPEC §5.6）。

继承 :class:`~app.infrastructure.database.unit_of_work.SqlAlchemyUnitOfWork`
（提供 ``__aenter__``/``__aexit__``/``commit``/``rollback`` 和 ``session`` 属性），
同时实现 :class:`~app.modules.user.application.port.UserUnitOfWork`
（提供 ``users`` Repository 访问）。

Use Case 通过 :class:`UserUnitOfWork` 端口使用此实现，不直接接触
``AsyncSession``（SPEC §5.6）。
"""

from __future__ import annotations

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.modules.user.application.port import UserUnitOfWork
from app.modules.user.infrastructure.repository import SqlAlchemyUserRepository


class SqlAlchemyUserUnitOfWork(SqlAlchemyUnitOfWork, UserUnitOfWork):
    """用户模块 SQLAlchemy 工作单元。

    在基类事务管理的基础上，通过 :attr:`users` 属性提供
    :class:`~app.modules.user.infrastructure.repository.SqlAlchemyUserRepository`
    访问。Repository 每次访问时从当前 Session 构造，不缓存状态。
    """

    @property
    def users(self) -> SqlAlchemyUserRepository:
        """当前事务作用域的用户 Repository。"""
        return SqlAlchemyUserRepository(self.session)
