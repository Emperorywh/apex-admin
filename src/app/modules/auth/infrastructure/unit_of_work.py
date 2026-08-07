"""认证模块工作单元实现（SPEC §5.6）。

继承 :class:`~app.infrastructure.database.unit_of_work.SqlAlchemyUnitOfWork`
（提供 ``__aenter__``/``__aexit__``/``commit``/``rollback`` 和 ``session`` 属性），
同时实现 :class:`~app.modules.auth.application.port.AuthUnitOfWork`
（提供 ``sessions``、``access_tokens``、``refresh_tokens`` 和 ``users``
Repository 访问）。

包含 ``users`` 属性以在同一事务中查询用户、校验密码并升级哈希
（SPEC §12.1：check_needs_rehash 必须在同一事务中完成）。
"""

from __future__ import annotations

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.modules.auth.application.port import AuthUnitOfWork
from app.modules.auth.infrastructure.login_attempt_repository import (
    SqlAlchemyLoginAttemptRepository,
)
from app.modules.auth.infrastructure.repository import (
    SqlAlchemyAccessTokenRepository,
    SqlAlchemyRefreshTokenRepository,
    SqlAlchemySessionRepository,
)
from app.modules.user.application.port import UserRepository
from app.modules.user.infrastructure.repository import SqlAlchemyUserRepository


class SqlAlchemyAuthUnitOfWork(SqlAlchemyUnitOfWork, AuthUnitOfWork):
    """认证模块 SQLAlchemy 工作单元。

    在基类事务管理的基础上，通过属性提供认证模块的三个 Repository
    和跨模块访问的用户 Repository。

    ``users`` 属性复用用户模块的 :class:`SqlAlchemyUserRepository`，
    在当前会话内执行用户查询和更新（SPEC §5.6：同一事务）。
    """

    @property
    def users(self) -> UserRepository:
        """当前事务作用域的用户 Repository（跨模块访问）。"""
        return SqlAlchemyUserRepository(self.session)

    @property
    def sessions(self) -> SqlAlchemySessionRepository:
        """当前事务作用域的会话 Repository。"""
        return SqlAlchemySessionRepository(self.session)

    @property
    def access_tokens(self) -> SqlAlchemyAccessTokenRepository:
        """当前事务作用域的 Access Token Repository。"""
        return SqlAlchemyAccessTokenRepository(self.session)

    @property
    def refresh_tokens(self) -> SqlAlchemyRefreshTokenRepository:
        """当前事务作用域的 Refresh Token Repository。"""
        return SqlAlchemyRefreshTokenRepository(self.session)

    @property
    def login_attempts(self) -> SqlAlchemyLoginAttemptRepository:
        """当前事务作用域的登录失败记录 Repository（SPEC §12.4）。"""
        return SqlAlchemyLoginAttemptRepository(self.session)
