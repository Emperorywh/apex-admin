"""用户 Repository Port — SPEC 5.2 / 5.6 / 8.1.

SPEC 5.2: "Repository、Unit of Work、文件存储和外部服务 Port
由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。
SPEC 8.1: "通过 Unit of Work 提供用例级 AsyncSession，不向 Router 暴露数据库会话"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port
（SPEC 5.2: "Infrastructure 只实现内层 Port"）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.api.pagination import SortField
    from app.modules.identity.models import User, UserStatus


class UserRepository(ABC):
    """用户 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型（如 AsyncSession、Select 等），
    确保内层不感知具体 ORM（SPEC 5.2 / 8.1）。

    返回值为领域实体 ``User``，不是 ORM 模型，
    实现 DTO/领域对象/ORM 模型职责分离（SPEC 5.2）。
    """

    @abstractmethod
    async def add(self, user: User) -> None:
        """添加新用户到当前事务.

        SPEC 8.3: "唯一性规则优先由数据库唯一约束保证"。
        用户名冲突时由数据库唯一约束拦截，由 Adapter 翻译为
        ``UserAlreadyExistsError``。

        参数:
            user: 待添加的领域实体。
        """

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> User | None:
        """按 ID 查询用户.

        参数:
            user_id: 用户 UUID。

        返回:
            领域实体；不存在时返回 None。
        """

    @abstractmethod
    async def get_by_username(self, username: str) -> User | None:
        """按用户名查询用户.

        参数:
            username: 用户名/登录账号。

        返回:
            领域实体；不存在时返回 None。
        """

    @abstractmethod
    async def list_users(
        self,
        *,
        offset: int,
        limit: int,
        sort_fields: list[SortField],
        status_filter: UserStatus | None,
    ) -> tuple[list[User], int]:
        """分页查询用户列表.

        SPEC 9.4: 排序字段使用白名单校验后的 ``SortField`` 列表。
        筛选字段由具体模块显式声明（SPEC 9.4）。

        参数:
            offset:        SQL OFFSET 值（零基）。
            limit:         SQL LIMIT 值。
            sort_fields:   已解析的排序字段列表（白名单已校验）。
            status_filter: 用户状态筛选（None 表示不筛选）。

        返回:
            (用户列表, 总数) 元组。
        """

    @abstractmethod
    async def save(self, user: User) -> None:
        """保存用户变更到当前事务.

        参数:
            user: 更新后的领域实体。
        """

    @abstractmethod
    async def delete_by_id(self, user_id: UUID) -> bool:
        """按 ID 物理删除用户.

        SPEC 11.3: 物理删除受审计记录保护。此方法仅执行数据库删除，
        审计记录存在性检查由 Use Case 在调用前完成。

        参数:
            user_id: 用户 UUID。

        返回:
            删除成功返回 True；用户不存在返回 False。
        """
