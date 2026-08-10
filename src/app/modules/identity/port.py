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
    from datetime import datetime
    from uuid import UUID

    from app.core.api.pagination import SortField
    from app.modules.identity.models import User, UserAuthInfo, UserStatus


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


class UserAuthPort(ABC):
    """用户认证信息 Port — 跨模块公开（SPEC 5.2 / 5.5 / 12.1 / 12.3）.

    SPEC 5.5: "模块依赖只允许指向其他模块的公开 Application Port"。
    auth 模块声明对 identity 的必需依赖，通过此 Port 查询用户认证相关数据，
    不直接访问 identity 的数据表或 ORM 模型（SPEC 5.5: "禁止跨模块直接操作
    对方的数据表、ORM 模型和内部函数"）。

    SPEC 12.1: 登录前检查用户状态、密码验证、rehash 升级（同事务）。
    SPEC 12.3: 认证依赖每请求校验用户启用状态。

    此 Port 返回 ``UserAuthInfo`` 投影（最小字段集），不暴露完整 ``User`` 实体，
    最小化跨模块数据耦合。
    """

    @abstractmethod
    async def get_auth_info_by_username(self, username: str) -> UserAuthInfo | None:
        """按用户名查询认证信息 — SPEC 12.1 登录用.

        参数:
            username: 用户名/登录账号。

        返回:
            认证信息投影；用户不存在返回 None。
        """

    @abstractmethod
    async def get_status_by_id(self, user_id: UUID) -> UserStatus | None:
        """按 ID 查询用户状态 — SPEC 12.3 认证依赖用.

        参数:
            user_id: 用户 ID。

        返回:
            用户状态；用户不存在返回 None。
        """

    @abstractmethod
    async def update_login_state(
        self,
        user_id: UUID,
        *,
        last_login_at: datetime,
        new_password_hash: str | None = None,
    ) -> None:
        """更新用户登录状态 — 同事务（SPEC 12.1）.

        SPEC 12.1: "登录成功时使用 check_needs_rehash 判断并在同一事务中
        升级旧参数哈希"。

        参数:
            user_id:           用户 ID。
            last_login_at:     最近登录时间（UTC）。
            new_password_hash: 新密码哈希（rehash 升级时提供，否则 None）。
        """

    @abstractmethod
    async def count_active_users_by_ids(self, user_ids: set[UUID]) -> int:
        """查询给定用户 ID 集合中处于启用状态的用户数量 — SPEC 13.4.

        用于最后超管保护——统计活跃超管数量时，需检查用户状态。

        参数:
            user_ids: 用户 ID 集合。

        返回:
            集合中处于 ACTIVE 状态的用户数量。
        """
