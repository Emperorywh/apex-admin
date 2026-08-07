"""RBAC 模块 Application Port（SPEC §5.2、§5.5、§5.6、§13）。

定义以下端口：

1. :class:`RbacApplicationPort` — 模块公开的应用服务接口，
   其他模块依赖此接口与 RBAC 模块协作（SPEC §5.5 ``application_port``）。
2. :class:`RoleRepository` — 角色数据访问端口。
3. :class:`UserRoleRepository` — 用户-角色关系数据访问端口。
4. :class:`RolePermissionRepository` — 角色-权限关系数据访问端口。
5. :class:`RbacUnitOfWork` — 扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，
   在事务作用域内提供全部 Repository 访问（SPEC §5.6）。

RBAC 模块依赖用户模块和认证模块（在同一事务中查询用户、会话和 Token
用于统一认证和权限加载）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.modules.rbac.domain.model import Role
from app.ports.unit_of_work import UnitOfWork

# ---------------------------------------------------------------------------
# 认证用户上下文（SPEC §13.3：统一认证依赖返回的上下文）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthenticatedUser:
    """已认证用户上下文（SPEC §13.3）。

    由统一 :func:`~app.modules.rbac.dependencies.get_current_user` 依赖返回，
    包含已验证的用户 ID、会话 ID 和从数据库加载的权限集合。

    权限基于 DB 实时加载，不使用 Token 缓存（SPEC §13.3：
    权限变更事务提交后，后续受保护请求立即读取并使用新的权限关系）。

    Attributes:
        user_id: 已验证的用户 UUID
        session_id: 已验证的会话 UUID
        permissions: 用户全部启用角色的权限点编码并集
        is_super_admin: 用户是否拥有超级管理员角色
        role_codes: 用户全部启用角色的编码集合
    """

    user_id: UUID
    session_id: UUID
    permissions: frozenset[str]
    is_super_admin: bool
    role_codes: frozenset[str]


# ---------------------------------------------------------------------------
# Application Port
# ---------------------------------------------------------------------------


class RbacApplicationPort(ABC):
    """RBAC 模块公开 Application Port（SPEC §5.5、§13）。

    其他模块依赖此接口与 RBAC 模块协作。跨模块调用只能通过公开的
    Application Port 完成（SPEC §5.1）。

    此接口不得提交、回滚或开启隐藏事务（SPEC §5.6）。
    """

    # ------------------------------------------------------------------
    # 角色 CRUD（SPEC §13.2）
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_role(
        self,
        *,
        code: str,
        name: str,
        description: str | None,
        is_super_admin: bool,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """创建角色（SPEC §13.2）。"""

    @abstractmethod
    async def get_role(self, role_id: UUID) -> Role:
        """查询角色详情（SPEC §13.2）。"""

    @abstractmethod
    async def list_roles(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[Role], int]:
        """分页查询角色列表（SPEC §13.2）。"""

    @abstractmethod
    async def update_role(
        self,
        *,
        role_id: UUID,
        field_updates: dict[str, str | None],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """更新角色（SPEC §13.2）。"""

    @abstractmethod
    async def enable_role(
        self,
        *,
        role_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """启用角色（SPEC §13.2）。"""

    @abstractmethod
    async def disable_role(
        self,
        *,
        role_id: UUID,
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> Role:
        """禁用角色（SPEC §13.2）。"""

    # ------------------------------------------------------------------
    # 角色-权限分配（SPEC §13.2）
    # ------------------------------------------------------------------

    @abstractmethod
    async def assign_permissions_to_role(
        self,
        *,
        role_id: UUID,
        permission_codes: frozenset[str],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> frozenset[str]:
        """为角色分配权限点（SPEC §13.2）。

        使用全量替换语义：传入的权限集合完全替换角色现有权限。
        普通管理员只能授予自身范围内的权限（SPEC §13.2）。
        """

    @abstractmethod
    async def get_role_permissions(self, role_id: UUID) -> frozenset[str]:
        """查询角色的权限点编码集合。"""

    # ------------------------------------------------------------------
    # 用户-角色分配（SPEC §13.2）
    # ------------------------------------------------------------------

    @abstractmethod
    async def assign_roles_to_user(
        self,
        *,
        user_id: UUID,
        role_codes: frozenset[str],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> None:
        """为用户分配角色（SPEC §13.2）。

        使用增量语义：在用户现有角色基础上追加指定角色。
        普通管理员只能授予自身范围内的角色，只能管理范围是自身子集的用户
        （SPEC §13.2）。
        """

    @abstractmethod
    async def remove_roles_from_user(
        self,
        *,
        user_id: UUID,
        role_codes: frozenset[str],
        current_time: datetime,
        actor_id: UUID | None = None,
    ) -> None:
        """移除用户角色（SPEC §13.2）。

        普通管理员只能管理范围是自身子集的用户（SPEC §13.2）。
        禁止移除导致系统失去最后一个可用超级管理员的操作（SPEC §13.4）。
        """

    @abstractmethod
    async def get_role_members(self, role_id: UUID) -> list[UUID]:
        """查询角色成员列表（SPEC §13.2）。"""

    @abstractmethod
    async def get_user_roles(self, user_id: UUID) -> list[Role]:
        """查询用户的角色列表。"""

    # ------------------------------------------------------------------
    # 权限查询（供统一认证依赖使用，SPEC §13.3）
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_user_permissions(self, user_id: UUID) -> frozenset[str]:
        """查询用户全部启用角色的权限点编码并集（SPEC §13.2 管理范围）。"""

    @abstractmethod
    async def is_user_super_admin(self, user_id: UUID) -> bool:
        """判断用户是否拥有超级管理员角色（SPEC §13.4）。"""


# ---------------------------------------------------------------------------
# Repository Ports
# ---------------------------------------------------------------------------


class RoleRepository(ABC):
    """角色数据访问端口（SPEC §5.2）。"""

    @abstractmethod
    async def add(self, entity: Role) -> None:
        """将角色实体添加到当前事务作用域。"""

    @abstractmethod
    async def get_by_id(self, role_id: UUID) -> Role | None:
        """按 ID 查询角色。"""

    @abstractmethod
    async def get_by_code(self, code: str) -> Role | None:
        """按编码查询角色。"""

    @abstractmethod
    async def count(self) -> int:
        """返回角色总数。"""

    @abstractmethod
    async def list_paginated(
        self,
        offset: int,
        limit: int,
    ) -> list[Role]:
        """分页查询角色列表，按创建时间降序排列。"""

    @abstractmethod
    async def update(self, entity: Role) -> None:
        """更新角色实体到当前事务作用域。"""

    @abstractmethod
    async def list_all(self) -> list[Role]:
        """查询全部角色（用于超级管理员检查等内部逻辑）。"""


class UserRoleRepository(ABC):
    """用户-角色关系数据访问端口（SPEC §5.2、§13.1）。"""

    @abstractmethod
    async def assign(
        self,
        *,
        user_id: UUID,
        role_id: UUID,
        assigned_at: datetime,
        assigned_by: UUID | None = None,
    ) -> None:
        """为用户分配角色（幂等：已存在时不报错）。"""

    @abstractmethod
    async def remove(self, user_id: UUID, role_id: UUID) -> None:
        """移除用户角色（幂等：不存在时不报错）。"""

    @abstractmethod
    async def get_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        """查询用户的全部角色 ID（含禁用角色）。"""

    @abstractmethod
    async def get_active_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        """查询用户的启用角色 ID（仅 status=active）。"""

    @abstractmethod
    async def get_user_ids_for_role(self, role_id: UUID) -> list[UUID]:
        """查询角色的全部成员用户 ID。"""

    @abstractmethod
    async def get_super_admin_user_ids(self) -> list[UUID]:
        """查询拥有超级管理员角色的全部用户 ID。"""


class RolePermissionRepository(ABC):
    """角色-权限关系数据访问端口（SPEC §5.2、§13.1）。"""

    @abstractmethod
    async def set_for_role(
        self,
        role_id: UUID,
        permission_codes: frozenset[str],
    ) -> None:
        """全量替换角色的权限点集合。"""

    @abstractmethod
    async def get_for_role(self, role_id: UUID) -> frozenset[str]:
        """查询角色的权限点编码集合。"""

    @abstractmethod
    async def get_for_user(self, user_id: UUID) -> frozenset[str]:
        """查询用户全部启用角色的权限点编码并集（SPEC §13.2 管理范围）。"""


# ---------------------------------------------------------------------------
# Unit of Work Port
# ---------------------------------------------------------------------------


class RbacUnitOfWork(UnitOfWork):
    """RBAC 模块工作单元端口（SPEC §5.6）。

    扩展 :class:`~app.ports.unit_of_work.UnitOfWork`，在事务作用域内
    提供角色、用户-角色和角色-权限 Repository 访问。

    同时提供跨模块访问的 ``users``、``sessions`` 和 ``access_tokens``
    Repository，用于统一认证依赖在单个事务中完成 Token 校验和权限加载
    （SPEC §5.6、§13.3）。
    """

    @property
    @abstractmethod
    def roles(self) -> RoleRepository:
        """当前事务作用域的角色 Repository。"""

    @property
    @abstractmethod
    def user_roles(self) -> UserRoleRepository:
        """当前事务作用域的用户-角色 Repository。"""

    @property
    @abstractmethod
    def role_permissions(self) -> RolePermissionRepository:
        """当前事务作用域的角色-权限 Repository。"""
