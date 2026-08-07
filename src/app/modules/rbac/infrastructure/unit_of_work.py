"""RBAC 模块工作单元实现（SPEC §5.6）。

继承 :class:`~app.infrastructure.database.unit_of_work.SqlAlchemyUnitOfWork`
（提供 ``__aenter__``/``__aexit__``/``commit``/``rollback`` 和 ``session`` 属性），
同时实现 :class:`~app.modules.rbac.application.port.RbacUnitOfWork`
（提供 ``roles``、``user_roles`` 和 ``role_permissions`` Repository 访问）。
"""

from __future__ import annotations

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.modules.rbac.application.port import RbacUnitOfWork
from app.modules.rbac.infrastructure.repository import (
    SqlAlchemyRolePermissionRepository,
    SqlAlchemyRoleRepository,
    SqlAlchemyUserRoleRepository,
)


class SqlAlchemyRbacUnitOfWork(SqlAlchemyUnitOfWork, RbacUnitOfWork):
    """RBAC 模块 SQLAlchemy 工作单元。

    在基类事务管理的基础上，通过属性提供 RBAC 模块的三个 Repository。
    Repository 每次访问时从当前 Session 构造，不缓存状态。
    """

    @property
    def roles(self) -> SqlAlchemyRoleRepository:
        """当前事务作用域的角色 Repository。"""
        return SqlAlchemyRoleRepository(self.session)

    @property
    def user_roles(self) -> SqlAlchemyUserRoleRepository:
        """当前事务作用域的用户-角色 Repository。"""
        return SqlAlchemyUserRoleRepository(self.session)

    @property
    def role_permissions(self) -> SqlAlchemyRolePermissionRepository:
        """当前事务作用域的角色-权限 Repository。"""
        return SqlAlchemyRolePermissionRepository(self.session)
