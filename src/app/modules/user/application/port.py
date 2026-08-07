"""用户模块 Application Port（SPEC §5.2、§5.5、§5.6、§11.1）。

定义四种端口：

1. :class:`UserApplicationPort` — 模块公开的应用服务接口，
   其他模块依赖此接口与用户模块协作（SPEC §5.5 ``application_port``）。
2. :class:`UserRepository` — 数据访问端口，Use Case 依赖此接口，
   Infrastructure 层的 Repository Adapter 实现此接口（SPEC §5.2 调用流）。
3. :class:`UserUnitOfWork` — 扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，
   在事务作用域内提供 :class:`UserRepository` 访问（SPEC §5.6）。
4. :class:`LastSuperAdminCheck` — 超级管理员保护端口，由 RBAC 模块
   （TASK-018）实现，用户模块在禁用用户前通过此端口检查
   是否为系统最后一个可用超级管理员（SPEC §11.1、§13.4）。

端口只定义接口，不包含运行时副作用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.modules.user.domain.model import User
from app.ports.unit_of_work import UnitOfWork


class UserApplicationPort(ABC):
    """用户模块公开 Application Port（SPEC §5.5）。

    其他模块依赖此接口与用户模块协作。跨模块调用只能通过公开的
    Application Port 完成（SPEC §5.1）。

    此接口不得提交、回滚或开启隐藏事务（SPEC §5.6）。
    """

    @abstractmethod
    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        phone: str | None = None,
        email: str | None = None,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """创建用户（SPEC §11.1）。

        Args:
            username: 用户名（须通过用户名策略校验）
            display_name: 显示名称
            password: 明文密码（须通过密码策略校验）
            phone: 手机号（可选）
            email: 邮箱（可选）
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID（审计字段）

        Returns:
            已创建的 :class:`User` 实体
        """

    @abstractmethod
    async def get_user(self, user_id: UUID) -> User:
        """查询用户详情（SPEC §11.1）。

        Args:
            user_id: 用户 UUID

        Returns:
            匹配的 :class:`User` 实体

        Raises:
            NotFoundError: 用户不存在
        """

    @abstractmethod
    async def list_users(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[User], int]:
        """分页查询用户列表（SPEC §11.1）。

        Args:
            page: 页码，从 1 开始
            page_size: 每页条数

        Returns:
            (当前页用户列表, 总数) 二元组
        """

    @abstractmethod
    async def update_user_profile(
        self,
        *,
        user_id: UUID,
        field_updates: dict[str, str | None],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """更新用户基本资料（SPEC §11.1）。

        部分更新：仅修改 ``field_updates`` 中包含的字段。

        Args:
            user_id: 用户 UUID
            field_updates: 字段更新字典
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID

        Returns:
            更新后的 :class:`User` 实体
        """

    @abstractmethod
    async def enable_user(
        self,
        *,
        user_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """启用用户（SPEC §11.1）。

        Args:
            user_id: 用户 UUID
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID

        Returns:
            启用后的 :class:`User` 实体
        """

    @abstractmethod
    async def disable_user(
        self,
        *,
        user_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """禁用用户（SPEC §11.1）。

        禁止禁用系统最后一个可用超级管理员（SPEC §11.1、§13.4）。

        Args:
            user_id: 用户 UUID
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID

        Returns:
            禁用后的 :class:`User` 实体

        Raises:
            ConflictError: 该用户是系统最后一个可用超级管理员
        """

    @abstractmethod
    async def reset_password(
        self,
        *,
        user_id: UUID,
        new_password: str,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> User:
        """管理员重置用户密码（SPEC §11.1）。

        管理员重置密码后，认证模块（TASK-015）吊销该用户全部会话
        （SPEC §12.3）。

        Args:
            user_id: 用户 UUID
            new_password: 新明文密码（须通过密码策略校验）
            current_time: 当前 UTC 时间
            actor_id: 操作者 ID

        Returns:
            更新后的 :class:`User` 实体
        """

    @abstractmethod
    async def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
        current_time: datetime,
    ) -> User:
        """用户自助修改密码（SPEC §11.1）。

        需验证当前密码正确。用户主动修改密码时保留当前会话并吊销其他会话
        （SPEC §12.3）。

        Args:
            user_id: 用户 UUID
            current_password: 当前明文密码
            new_password: 新明文密码（须通过密码策略校验）
            current_time: 当前 UTC 时间

        Returns:
            更新后的 :class:`User` 实体

        Raises:
            ParameterError: 当前密码不正确或新密码不合规
        """

    @abstractmethod
    async def get_self_profile(self, user_id: UUID) -> User:
        """用户自助查询资料（SPEC §11.1）。

        Args:
            user_id: 当前用户 UUID

        Returns:
            当前用户的 :class:`User` 实体

        Raises:
            NotFoundError: 用户不存在
        """

    @abstractmethod
    async def update_self_profile(
        self,
        *,
        user_id: UUID,
        field_updates: dict[str, str | None],
        current_time: datetime,
    ) -> User:
        """用户自助更新允许修改的资料（SPEC §11.1）。

        允许自助修改的字段：显示名称、手机号、邮箱。
        不允许自助修改：用户名、状态、密码哈希。

        Args:
            user_id: 当前用户 UUID
            field_updates: 字段更新字典
            current_time: 当前 UTC 时间

        Returns:
            更新后的 :class:`User` 实体
        """


class UserRepository(ABC):
    """用户模块数据访问端口（SPEC §5.2）。

    Use Case 依赖此接口执行持久化操作。Infrastructure 层的
    :class:`~app.modules.user.infrastructure.repository.SqlAlchemyUserRepository`
    实现此接口。

    所有方法在当前 Unit of Work 的事务作用域内执行，
    不自行提交或回滚（SPEC §5.6）。
    """

    @abstractmethod
    async def add(self, entity: User) -> None:
        """将用户实体添加到当前事务作用域。"""

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> User | None:
        """按 ID 查询单个用户。"""

    @abstractmethod
    async def get_by_username(self, username: str) -> User | None:
        """按用户名查询单个用户（用于唯一性检查）。"""

    @abstractmethod
    async def count(self) -> int:
        """返回用户总数。"""

    @abstractmethod
    async def list_paginated(
        self,
        offset: int,
        limit: int,
    ) -> list[User]:
        """分页查询用户列表，按创建时间降序排列。"""

    @abstractmethod
    async def update(self, entity: User) -> None:
        """更新用户实体到当前事务作用域。"""


class UserUnitOfWork(UnitOfWork):
    """用户模块工作单元端口（SPEC §5.6）。

    扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，在事务作用域内
    提供 :class:`UserRepository` 访问。

    Infrastructure 层的
    :class:`~app.modules.user.infrastructure.unit_of_work.SqlAlchemyUserUnitOfWork`
    实现此端口。
    """

    @property
    @abstractmethod
    def users(self) -> UserRepository:
        """当前事务作用域的用户 Repository。"""


class LastSuperAdminCheck(ABC):
    """最后一个可用超级管理员检查端口（SPEC §11.1、§13.4）。

    由 RBAC 模块（TASK-018）实现。用户模块在禁用或删除用户前
    通过此端口检查目标用户是否为系统最后一个可用超级管理员，
    若是则禁止操作（SPEC §13.4：防止系统失去最后一个可用超级管理员）。

    在 RBAC 模块实现前，使用默认实现返回 ``False``（不阻止任何操作），
    RBAC 实现后替换为查询角色和权限关系的真实实现。
    """

    @abstractmethod
    async def is_last_available_super_admin(self, user_id: UUID) -> bool:
        """判断指定用户是否为系统最后一个可用超级管理员。

        Args:
            user_id: 待检查的用户 UUID

        Returns:
            是最后一个可用超级管理员返回 ``True``
        """
