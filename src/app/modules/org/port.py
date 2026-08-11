"""组织模块 Repository Port — SPEC 5.2 / 5.6 / 8.1 / 14.1.

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
    用户组织关系在 TASK-020 实现；当前返回 0，保留接口供后续接线。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.modules.org.models import Department


class OrgRepository(ABC):
    """组织 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型，确保内层不感知具体 ORM。

    返回值为领域实体（``Department``），不是 ORM 模型。
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
        用户组织关系在 TASK-020 实现；当前返回 0，保留接口供后续接线。
        """
