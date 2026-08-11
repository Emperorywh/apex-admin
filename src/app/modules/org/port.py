"""组织模块 Repository Port — SPEC 5.2 / 5.6 / 8.1 / 14.1 / 14.2 / 14.3.

SPEC 5.2: "Repository、Unit of Work 由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port
（SPEC 5.2: "Infrastructure 只实现内层 Port"）。

循环防护（SPEC 14.1: "防止形成循环层级"）:
  - ``get_descendant_ids`` 查询部门的全部后代 ID，用于检测间接循环。
  - ``acquire_hierarchy_lock`` 获取事务级咨询锁，序列化并发层级调整，
    防止两个并发事务同时通过循环检查后形成循环。

SPEC 14.1 删除保护:
  - ``count_children`` 查询子部门数量。
  - ``count_users_in_department`` 查询部门关联的用户数量。

SPEC 14.2 岗位管理:
  - 岗位 CRUD 与启停。
  - 用户岗位分配与移除（幂等防重复）。

SPEC 14.3 用户组织关系:
  - 用户主部门关系的设置与解除。
  - 用户离职/禁用时清除组织关系。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.modules.org.models import (
        Department,
        Post,
        UserDepartmentInfo,
        UserPostInfo,
    )


class OrgRepository(ABC):
    """组织 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型，确保内层不感知具体 ORM。

    返回值为领域实体（``Department``、``Post``），不是 ORM 模型。
    """

    # ── 部门 CRUD ───────────────────────────────────────────────────────

    @abstractmethod
    async def add_department(self, department: Department) -> None:
        """添加新部门到当前事务.

        部门编码冲突时由数据库唯一约束拦截，翻译为
        ``DepartmentAlreadyExistsError``。

        参数:
            department: 待添加的领域实体。
        """

    @abstractmethod
    async def get_department_by_id(self, department_id: UUID) -> Department | None:
        """按 ID 查询部门，返回领域实体或 None。"""

    @abstractmethod
    async def get_department_by_code(self, code: str) -> Department | None:
        """按编码查询部门，返回领域实体或 None。"""

    @abstractmethod
    async def save_department(self, department: Department) -> None:
        """保存部门变更到当前事务.

        参数:
            department: 更新后的领域实体。
        """

    @abstractmethod
    async def delete_department_by_id(self, department_id: UUID) -> bool:
        """按 ID 物理删除部门，返回是否删除成功。

        参数:
            department_id: 部门 ID。

        返回:
            删除成功返回 True；部门不存在返回 False。
        """

    @abstractmethod
    async def list_all_departments(
        self,
        *,
        include_disabled: bool = True,
    ) -> list[Department]:
        """查询全部部门（用于构建树结构）.

        参数:
            include_disabled: 是否包含禁用状态的部门。

        返回:
            部门列表（未排序，由调用方构建树）。
        """

    # ── 循环防护 ────────────────────────────────────────────────────────

    @abstractmethod
    async def get_descendant_ids(self, department_id: UUID) -> set[UUID]:
        """查询部门的全部后代 ID（递归）— 循环防护用.

        用于检测间接循环：当调整层级时，目标父部门不能是当前部门的后代。
        查询从 ``department_id`` 的直接子部门开始，递归遍历全部后代。

        参数:
            department_id: 起始部门 ID。

        返回:
            全部后代部门 ID 集合（不含起始部门自身）。
        """

    @abstractmethod
    async def acquire_hierarchy_lock(self) -> None:
        """获取事务级咨询锁 — 序列化并发层级调整（SPEC 14.1）.

        SPEC 14.1: "部门和菜单并发调整测试证明无法形成循环"。

        使用 PostgreSQL 事务级咨询锁（``pg_advisory_xact_lock``），
        确保同一时间只有一个层级调整事务在进行。锁在事务提交或回滚时
        自动释放。

        这防止了两个并发事务同时通过循环检查后形成循环的竞态条件。
        """

    # ── 删除保护 ────────────────────────────────────────────────────────

    @abstractmethod
    async def count_children(self, department_id: UUID) -> int:
        """查询部门的直接子部门数量 — 删除保护用.

        SPEC 14.1: "有用户或子部门时的删除规则明确"。
        """

    @abstractmethod
    async def count_users_in_department(self, department_id: UUID) -> int:
        """查询部门关联的用户数量 — 删除保护用.

        SPEC 14.1: "有用户或子部门时的删除规则明确"。
        """

    # ── 岗位 CRUD — SPEC 14.2 ───────────────────────────────────────────

    @abstractmethod
    async def add_post(self, post: Post) -> None:
        """添加新岗位到当前事务.

        岗位编码冲突时由数据库唯一约束拦截，翻译为
        ``PostAlreadyExistsError``。
        """

    @abstractmethod
    async def get_post_by_id(self, post_id: UUID) -> Post | None:
        """按 ID 查询岗位，返回领域实体或 None。"""

    @abstractmethod
    async def get_post_by_code(self, code: str) -> Post | None:
        """按编码查询岗位，返回领域实体或 None。"""

    @abstractmethod
    async def save_post(self, post: Post) -> None:
        """保存岗位变更到当前事务."""

    @abstractmethod
    async def delete_post_by_id(self, post_id: UUID) -> bool:
        """按 ID 物理删除岗位，返回是否删除成功。"""

    @abstractmethod
    async def list_posts(
        self,
        *,
        include_disabled: bool = True,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[Post], int]:
        """查询岗位列表（分页）.

        参数:
            include_disabled: 是否包含禁用状态的岗位。
            offset: SQL OFFSET。
            limit: SQL LIMIT。

        返回:
            (岗位列表, 总数) 元组。
        """

    @abstractmethod
    async def count_users_for_post(self, post_id: UUID) -> int:
        """查询岗位关联的用户数量 — 删除保护用（SPEC 14.2）。"""

    # ── 用户组织关系 — SPEC 14.2 / 14.3 ─────────────────────────────────

    @abstractmethod
    async def set_user_department(
        self,
        user_id: UUID,
        department_id: UUID,
        *,
        created_by: str | None,
        created_at: datetime,
    ) -> None:
        """设置用户主部门 — SPEC 14.3.

        SPEC 14.3: "用户具有明确的主部门"。
        基座默认仅主部门。如果用户已有主部门，抛出
        ``UserAlreadyHasDepartmentError``。

        唯一约束 ``(user_id)`` 在数据库层面保证一个用户仅一个主部门。
        """

    @abstractmethod
    async def get_user_department(self, user_id: UUID) -> UserDepartmentInfo | None:
        """查询用户的主部门关系投影。"""

    @abstractmethod
    async def remove_user_department(self, user_id: UUID) -> bool:
        """移除用户的主部门关系.

        返回:
            移除成功返回 True；关系不存在返回 False。
        """

    @abstractmethod
    async def assign_user_post(
        self,
        user_id: UUID,
        post_id: UUID,
        *,
        created_by: str | None,
        created_at: datetime,
    ) -> bool:
        """为用户分配岗位 — SPEC 14.2.

        SPEC 14.2: "为用户分配岗位"。
        唯一约束 ``(user_id, post_id)`` 保证幂等——已存在时返回 False（无操作），
        新建时返回 True。
        """

    @abstractmethod
    async def remove_user_post(self, user_id: UUID, post_id: UUID) -> bool:
        """移除用户岗位 — SPEC 14.2.

        SPEC 14.2: "移除用户岗位"。
        返回移除成功 True；关系不存在 False。
        """

    @abstractmethod
    async def list_user_posts(self, user_id: UUID) -> list[UserPostInfo]:
        """查询用户的全部岗位关系投影。"""

    @abstractmethod
    async def clear_user_org_relations(self, user_id: UUID) -> None:
        """清除用户全部组织关系（主部门 + 岗位）— SPEC 14.3.

        SPEC 14.3: "用户离职或禁用时组织关系按规则处理"。
        在 UserDisabled 事件处理器中调用。
        """


class UserOrgPort(ABC):
    """用户组织关系 Port — 跨模块公开（SPEC 5.2 / 5.5 / 14.3 / 11.1）.

    SPEC 5.5: "模块依赖只允许指向其他模块的公开 Application Port"。
    identity 模块声明对 org 的必需依赖，通过此 Port 查询用户的部门岗位关系，
    不直接访问 org 的数据表或 ORM 模型。

    SPEC 11.1: "通过 G3 后同时返回部门和岗位关系"。
    此 Port 供 identity 模块在用户详情中聚合返回部门与岗位关系。

    返回投影（``UserDepartmentInfo``、``UserPostInfo``），不暴露 ORM 模型。
    """

    @abstractmethod
    async def get_user_department(self, user_id: UUID) -> UserDepartmentInfo | None:
        """查询用户主部门关系投影 — 用户详情聚合用."""

    @abstractmethod
    async def list_user_posts(self, user_id: UUID) -> list[UserPostInfo]:
        """查询用户岗位关系列表投影 — 用户详情聚合用."""
