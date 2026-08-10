"""RBAC Repository Port — SPEC 5.2 / 5.6 / 8.1 / 13.1 / 13.2.

SPEC 5.2: "Repository、Unit of Work 由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port
（SPEC 5.2: "Infrastructure 只实现内层 Port"）。

``UserRbacPort`` 为公开的跨模块 Port，供 auth 模块（TASK-016）查询用户
有效权限集。此 Port 每次调用都查库，不使用 TTL 缓存
（SPEC 13.3: "权限变更事务提交后，后续受保护请求立即读取并使用新的权限关系"）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.core.api.pagination import SortField
    from app.modules.rbac.models import Permission, Role, RoleAssignment, RoleStatus


class RbacRepository(ABC):
    """RBAC Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型，确保内层不感知具体 ORM。

    返回值为领域实体（``Role``、``Permission``、``RoleAssignment``），
    不是 ORM 模型。
    """

    # ── 角色 ────────────────────────────────────────────────────────────

    @abstractmethod
    async def add_role(self, role: Role) -> None:
        """添加新角色到当前事务.

        角色编码冲突时由数据库唯一约束拦截，翻译为 ``RoleAlreadyExistsError``。

        参数:
            role: 待添加的领域实体。
        """

    @abstractmethod
    async def get_role_by_id(self, role_id: UUID) -> Role | None:
        """按 ID 查询角色，返回领域实体或 None。"""

    @abstractmethod
    async def get_roles_by_codes(self, codes: set[str]) -> list[Role]:
        """按编码集合查询角色.

        参数:
            codes: 角色编码集合。

        返回:
            匹配的角色实体列表。
        """

    @abstractmethod
    async def get_roles_by_ids(self, ids: set[UUID]) -> list[Role]:
        """按 ID 集合查询角色.

        参数:
            ids: 角色 ID 集合。

        返回:
            匹配的角色实体列表。
        """

    @abstractmethod
    async def list_roles(
        self,
        *,
        offset: int,
        limit: int,
        sort_fields: list[SortField],
        status_filter: RoleStatus | None,
    ) -> tuple[list[Role], int]:
        """分页查询角色列表.

        参数:
            offset:        SQL OFFSET 值（零基）。
            limit:         SQL LIMIT 值。
            sort_fields:   已解析的排序字段列表（白名单已校验）。
            status_filter: 角色状态筛选（None 表示不筛选）。

        返回:
            (角色列表, 总数) 元组。
        """

    @abstractmethod
    async def save_role(self, role: Role) -> None:
        """保存角色变更到当前事务.

        参数:
            role: 更新后的领域实体。
        """

    @abstractmethod
    async def delete_role_by_id(self, role_id: UUID) -> bool:
        """按 ID 物理删除角色，返回是否删除成功。

        参数:
            role_id: 角色 ID。

        返回:
            删除成功返回 True；角色不存在返回 False。
        """

    # ── 权限点 ──────────────────────────────────────────────────────────

    @abstractmethod
    async def get_permission_codes(self, codes: set[str]) -> list[Permission]:
        """按编码集合查询权限点.

        返回与给定编码匹配的权限点列表。用于验证角色分配的权限编码是否存在。

        参数:
            codes: 权限编码集合。

        返回:
            匹配的权限点列表。
        """

    @abstractmethod
    async def add_permission(self, permission: Permission) -> None:
        """添加新权限点到当前事务（用于 sync-permissions）。"""

    @abstractmethod
    async def update_permission(self, permission: Permission) -> None:
        """更新权限点（用于 sync-permissions）。"""

    @abstractmethod
    async def list_all_permissions(self) -> list[Permission]:
        """查询全部权限点。"""

    @abstractmethod
    async def delete_permissions_by_ids(self, ids: set[UUID]) -> int:
        """按 ID 集合删除权限点，返回删除数量（用于清理孤立权限点）。"""

    # ── 角色-权限点 ────────────────────────────────────────────────────

    @abstractmethod
    async def replace_role_permissions(
        self,
        role_id: UUID,
        permission_ids: set[UUID],
        *,
        now: object,
    ) -> None:
        """替换角色的全部权限点 — 全量覆盖.

        SPEC 13.2: "为角色分配权限点"。
        先删除角色现有全部权限关联，再插入新的关联记录。

        参数:
            role_id:        角色 ID。
            permission_ids: 权限点 ID 集合（全量替换）。
            now:            当前时间（UTC datetime）。
        """

    @abstractmethod
    async def get_role_permission_codes(self, role_id: UUID) -> list[str]:
        """查询角色已分配的权限编码列表。"""

    # ── 用户-角色 ──────────────────────────────────────────────────────

    @abstractmethod
    async def add_user_role(
        self,
        user_id: UUID,
        role_id: UUID,
        *,
        now: object,
        created_by: str | None,
    ) -> None:
        """添加用户角色关系.

        SPEC 13.2: "为用户分配角色"。
        重复分配时由复合主键拦截，翻译为 ``UserRoleAlreadyAssignedError``。
        """

    @abstractmethod
    async def remove_user_role(self, user_id: UUID, role_id: UUID) -> bool:
        """移除用户角色关系，返回是否移除成功。"""

    @abstractmethod
    async def list_user_roles(self, user_id: UUID) -> list[RoleAssignment]:
        """查询用户的全部角色分配记录。"""

    @abstractmethod
    async def list_role_members(
        self,
        role_id: UUID,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[RoleAssignment], int]:
        """分页查询角色成员.

        返回:
            (角色分配记录列表, 总数) 元组。
        """

    @abstractmethod
    async def count_role_members(self, role_id: UUID) -> int:
        """查询角色成员数量。"""

    @abstractmethod
    async def count_roles_for_user(self, user_id: UUID) -> int:
        """查询用户分配的角色数量。"""


class UserRbacPort(ABC):
    """用户 RBAC 信息 Port — 跨模块公开（SPEC 5.2 / 5.5 / 13.3）.

    SPEC 5.5: "模块依赖只允许指向其他模块的公开 Application Port"。
    auth 模块（TASK-016）通过此 Port 查询用户有效权限集。

    SPEC 13.3: "权限变更事务提交后，后续受保护请求立即读取并使用新的权限关系"。
    此 Port 每次调用都查库，不使用 TTL 缓存——变更提交后下一请求立即生效。

    SPEC 13.1: 被禁用角色的权限不计入用户有效权限集。
    """

    @abstractmethod
    async def get_effective_permission_codes(self, user_id: UUID) -> set[str]:
        """查询用户有效权限编码集合 — 每次查库，无缓存（SPEC 13.3）.

        有效权限 = 用户全部启用角色的权限点编码并集。
        被禁用角色的权限不计入（SPEC 13.1 / 13.2）。

        参数:
            user_id: 用户 ID。

        返回:
            有效权限编码集合。
        """

    @abstractmethod
    async def get_role_ids_by_user(self, user_id: UUID) -> list[UUID]:
        """查询用户全部角色 ID 列表.

        参数:
            user_id: 用户 ID。

        返回:
            角色 ID 列表。
        """

    @abstractmethod
    async def get_role_codes_by_user(self, user_id: UUID) -> set[str]:
        """查询用户全部角色编码集合 — SPEC 13.4.

        用于超管判定（检查是否拥有 ``super_admin`` 角色编码）。
        每次调用查库，无 TTL 缓存（SPEC 13.3）。

        参数:
            user_id: 用户 ID。

        返回:
            用户全部角色编码集合（含启用和禁用角色）。
        """

    @abstractmethod
    async def get_user_ids_by_role_code(self, role_code: str) -> set[UUID]:
        """查询拥有指定角色编码的全部用户 ID — SPEC 13.4.

        用于最后超管保护——统计拥有 ``super_admin`` 角色的用户数量。

        参数:
            role_code: 角色编码。

        返回:
            拥有该角色编码的全部用户 ID 集合。
        """
