"""菜单模块 Repository Port — SPEC 5.2 / 5.6 / 8.1 / 15.1 / 15.2.

SPEC 5.2: "Repository、Unit of Work 由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port
（SPEC 5.2: "Infrastructure 只实现内层 Port"）。

循环防护（SPEC 15.1: "防止形成循环层级"）:
  - ``get_descendant_ids`` 查询菜单的全部后代 ID，用于检测间接循环。
  - ``acquire_hierarchy_lock`` 获取事务级咨询锁，序列化并发层级调整，
    防止两个并发事务同时通过循环检查后形成循环。

SPEC 15.1 删除保护:
  - ``count_children`` 查询子菜单数量。

SPEC 15.2 角色菜单:
  - ``replace_role_menus`` 全量替换角色菜单。
  - ``remove_role_menu`` 移除单个角色菜单关联。
  - ``get_menu_ids_by_role_ids`` 按角色 ID 集合查询已分配菜单 ID。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.modules.menu.models import Menu


class MenuRepository(ABC):
    """菜单 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型，确保内层不感知具体 ORM。

    返回值为领域实体（``Menu``），不是 ORM 模型。
    """

    # ── 菜单 CRUD ───────────────────────────────────────────────────────

    @abstractmethod
    async def add_menu(self, menu: Menu) -> None:
        """添加新菜单到当前事务."""

    @abstractmethod
    async def get_menu_by_id(self, menu_id: UUID) -> Menu | None:
        """按 ID 查询菜单，返回领域实体或 None。"""

    @abstractmethod
    async def save_menu(self, menu: Menu) -> None:
        """保存菜单变更到当前事务."""

    @abstractmethod
    async def delete_menu_by_id(self, menu_id: UUID) -> bool:
        """按 ID 物理删除菜单，返回是否删除成功。"""

    @abstractmethod
    async def list_all_menus(
        self,
        *,
        include_disabled: bool = True,
    ) -> list[Menu]:
        """查询全部菜单（用于构建树结构）.

        参数:
            include_disabled: 是否包含禁用状态的菜单。

        返回:
            菜单列表（未排序，由调用方构建树）。
        """

    # ── 循环防护 ────────────────────────────────────────────────────────

    @abstractmethod
    async def get_descendant_ids(self, menu_id: UUID) -> set[UUID]:
        """查询菜单的全部后代 ID（递归）— 循环防护用.

        查询从 ``menu_id`` 的直接子菜单开始，递归遍历全部后代。

        参数:
            menu_id: 起始菜单 ID。

        返回:
            全部后代菜单 ID 集合（不含起始菜单自身）。
        """

    @abstractmethod
    async def acquire_hierarchy_lock(self) -> None:
        """获取事务级咨询锁 — 序列化并发层级调整（SPEC 15.1）.

        使用 PostgreSQL 事务级咨询锁（``pg_advisory_xact_lock``），
        确保同一时间只有一个层级调整事务在进行。锁在事务提交或回滚时
        自动释放。
        """

    # ── 删除保护 ────────────────────────────────────────────────────────

    @abstractmethod
    async def count_children(self, menu_id: UUID) -> int:
        """查询菜单的直接子菜单数量 — 删除保护用."""

    # ── 角色菜单关系 ────────────────────────────────────────────────────

    @abstractmethod
    async def replace_role_menus(
        self,
        role_id: UUID,
        menu_ids: set[UUID],
        *,
        now: object,
    ) -> None:
        """替换角色的全部菜单 — 全量覆盖.

        先删除角色现有全部菜单关联，再插入新的关联记录。
        全量替换天然幂等——相同输入多次调用结果一致。

        参数:
            role_id:  角色 ID。
            menu_ids: 菜单 ID 集合（全量替换）。
            now:      当前时间（UTC datetime）。
        """

    @abstractmethod
    async def remove_role_menu(self, role_id: UUID, menu_id: UUID) -> bool:
        """移除角色单个菜单关联.

        返回移除成功 True；关系不存在 False（幂等）。
        """

    @abstractmethod
    async def get_menu_ids_by_role_ids(self, role_ids: set[UUID]) -> set[UUID]:
        """按角色 ID 集合查询已分配的菜单 ID 集合.

        SPEC 15.2: 用于当前用户菜单树——查询用户启用角色已分配的菜单 ID。

        参数:
            role_ids: 角色 ID 集合。

        返回:
            已分配的菜单 ID 集合（去重）。
        """

    @abstractmethod
    async def get_menus_by_ids(self, menu_ids: set[UUID]) -> list[Menu]:
        """按 ID 集合查询菜单实体.

        仅返回启用状态的菜单（SPEC 15.2: 当前用户菜单树仅包含启用菜单）。

        参数:
            menu_ids: 菜单 ID 集合。

        返回:
            启用状态的菜单实体列表。
        """
