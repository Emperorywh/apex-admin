"""认证模块 Application Port（SPEC §5.2、§5.5、§5.6、§12）。

定义四种端口：

1. :class:`AuthApplicationPort` — 模块公开的应用服务接口，
   其他模块依赖此接口与认证模块协作（SPEC §5.5 ``application_port``）。
2. :class:`SessionRepository` — 会话数据访问端口。
3. :class:`AccessTokenRepository` — Access Token 摘要数据访问端口。
4. :class:`RefreshTokenRepository` — Refresh Token 摘要数据访问端口。
5. :class:`AuthUnitOfWork` — 扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，
   在事务作用域内提供会话、Token 和用户 Repository 访问（SPEC §5.6）。

认证模块依赖用户模块的 :class:`~app.modules.user.application.port.UserRepository`
端口在同一事务中查询用户、校验密码并升级哈希（SPEC §5.6：check_needs_rehash
升级必须在同一事务中完成）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.modules.auth.domain.model import (
    AccessTokenRecord,
    RefreshTokenRecord,
    Session,
)
from app.modules.user.application.port import UserRepository
from app.ports.unit_of_work import UnitOfWork


class AuthApplicationPort(ABC):
    """认证模块公开 Application Port（SPEC §5.5、§12.1、§12.3）。

    其他模块依赖此接口与认证模块协作。跨模块调用只能通过公开的
    Application Port 完成（SPEC §5.1）。

    此接口不得提交、回滚或开启隐藏事务（SPEC §5.6）。
    """

    @abstractmethod
    async def login(
        self,
        *,
        username: str,
        password: str,
        ip: str,
        user_agent: str,
        device: str | None,
        current_time: datetime,
    ) -> LoginResult:
        """账号密码登录（SPEC §12.1）。

        1. 按用户名查询用户
        2. 用户不存在时执行固定 Argon2id 虚拟哈希校验（SPEC §12.4）
        3. 检查用户状态（SPEC §12.1）
        4. Argon2id 验证密码
        5. ``check_needs_rehash`` 在同一事务中升级旧参数哈希（SPEC §12.1）
        6. 创建服务端会话（SPEC §12.3）
        7. 生成 Access Token（HMAC-SHA-256 摘要入库）
        8. 生成 Refresh Token（独立密钥 HMAC-SHA-256 摘要入库）

        Args:
            username: 用户名
            password: 明文密码
            ip: 客户端 IP
            user_agent: 客户端 User-Agent
            device: 设备标识（可选）
            current_time: 当前 UTC 时间

        Returns:
            :class:`LoginResult`，包含 Access Token、Refresh Token 明文
            和会话标识

        Raises:
            AuthenticationError: 用户名或密码不正确 / 用户已禁用
        """

    @abstractmethod
    async def logout(self, *, refresh_token: str, current_time: datetime) -> None:
        """退出登录（SPEC §12.3、§12.4）。

        通过 Refresh Token 摘要查找会话，吊销会话。Cookie 删除由路由层处理。

        Args:
            refresh_token: Refresh Token 明文（从 Cookie 读取）
            current_time: 当前 UTC 时间
        """


class LoginResult:
    """登录 Use Case 结果（SPEC §12.1、§12.2）。

    携带 Access Token 和 Refresh Token 的明文值，由路由层分别放入
    响应体（Access Token，一次性）和 HttpOnly Cookie（Refresh Token）。

    此类为普通 dataclass-like 结果对象，不持久化。Token 明文绝不入库。

    Attributes:
        access_token: Access Token 明文（仅在登录响应体中返回一次）
        refresh_token: Refresh Token 明文（仅通过 HttpOnly Cookie 传递）
        session_id: 新创建会话的 UUID
        user_id: 登录用户的 UUID
        access_token_expires_in: Access Token 有效期（秒），默认 900（15 分钟）
    """

    __slots__ = (
        "access_token",
        "refresh_token",
        "session_id",
        "user_id",
        "access_token_expires_in",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        access_token: str,
        refresh_token: str,
        session_id: UUID,
        user_id: UUID,
        access_token_expires_in: int,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.session_id = session_id
        self.user_id = user_id
        self.access_token_expires_in = access_token_expires_in


class SessionRepository(ABC):
    """会话数据访问端口（SPEC §5.2、§12.3）。"""

    @abstractmethod
    async def add(self, entity: Session) -> None:
        """添加会话实体到当前事务作用域。"""

    @abstractmethod
    async def get_by_id(self, session_id: UUID) -> Session | None:
        """按 ID 查询会话。"""

    @abstractmethod
    async def list_by_user(self, user_id: UUID) -> list[Session]:
        """查询用户的活动会话列表。"""

    @abstractmethod
    async def update(self, entity: Session) -> None:
        """更新会话实体到当前事务作用域。"""


class AccessTokenRepository(ABC):
    """Access Token 摘要数据访问端口（SPEC §5.2、§12.2）。"""

    @abstractmethod
    async def add(self, entity: AccessTokenRecord) -> None:
        """添加 Access Token 摘要记录到当前事务作用域。"""

    @abstractmethod
    async def get_by_digest(self, digest: str) -> AccessTokenRecord | None:
        """按 HMAC 摘要查询 Access Token 记录。"""

    @abstractmethod
    async def delete_by_session(self, session_id: UUID) -> None:
        """删除指定会话的全部 Access Token 记录。"""


class RefreshTokenRepository(ABC):
    """Refresh Token 摘要数据访问端口（SPEC §5.2、§12.2）。"""

    @abstractmethod
    async def add(self, entity: RefreshTokenRecord) -> None:
        """添加 Refresh Token 摘要记录到当前事务作用域。"""

    @abstractmethod
    async def get_by_digest(self, digest: str) -> RefreshTokenRecord | None:
        """按 HMAC 摘要查询 Refresh Token 记录。"""


class AuthUnitOfWork(UnitOfWork):
    """认证模块工作单元端口（SPEC §5.6）。

    扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，在事务作用域内提供
    会话、Token 和用户 Repository 访问。

    包含 ``users`` 属性以在同一事务中查询用户、校验密码并升级哈希
    （SPEC §12.1：check_needs_rehash 必须在同一事务中完成）。

    Infrastructure 层的
    :class:`~app.modules.auth.infrastructure.unit_of_work.SqlAlchemyAuthUnitOfWork`
    实现此端口。
    """

    @property
    @abstractmethod
    def users(self) -> UserRepository:
        """当前事务作用域的用户 Repository（跨模块访问，SPEC §5.6）。"""

    @property
    @abstractmethod
    def sessions(self) -> SessionRepository:
        """当前事务作用域的会话 Repository。"""

    @property
    @abstractmethod
    def access_tokens(self) -> AccessTokenRepository:
        """当前事务作用域的 Access Token Repository。"""

    @property
    @abstractmethod
    def refresh_tokens(self) -> RefreshTokenRepository:
        """当前事务作用域的 Refresh Token Repository。"""
