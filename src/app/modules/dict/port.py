"""数据字典 Repository Port 与引用登记 Port — SPEC 5.2 / 5.6 / 17.1 / 17.2.

SPEC 5.2: "Repository、Unit of Work 由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port。

SPEC 17.1:
  - ``DictRepository`` 提供字典类型的 CRUD、编码唯一性检查与删除。
  - ``ReferenceRegistryPort`` 供业务模块登记对字典类型的引用（业务模块编码
    + 资源标识），删除字典类型时检查是否存在引用登记。

SPEC 17.2:
  - ``DictRepository`` 提供字典项的 CRUD、稳定值唯一性检查。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.modules.dict.models import DictItem, DictType


class DictRepository(ABC):
    """数据字典 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型。
    返回值为领域实体（``DictType``、``DictItem``），不是 ORM 模型。
    """

    # ── 字典类型 CRUD — SPEC 17.1 ───────────────────────────────────────

    @abstractmethod
    async def add_dict_type(self, dict_type: DictType) -> None:
        """添加新字典类型到当前事务.

        编码冲突时由数据库唯一约束拦截。
        """

    @abstractmethod
    async def get_dict_type_by_id(self, dict_type_id: UUID) -> DictType | None:
        """按 ID 查询字典类型，返回领域实体或 None。"""

    @abstractmethod
    async def get_dict_type_by_code(self, code: str) -> DictType | None:
        """按编码查询字典类型 — 唯一性检查用.

        SPEC 17.1: 字典编码全局唯一。
        """

    @abstractmethod
    async def save_dict_type(self, dict_type: DictType) -> None:
        """保存字典类型变更到当前事务."""

    @abstractmethod
    async def delete_dict_type_by_id(self, dict_type_id: UUID) -> bool:
        """按 ID 物理删除字典类型，返回是否删除成功."""

    @abstractmethod
    async def list_dict_types(
        self,
        *,
        include_disabled: bool = True,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[DictType], int]:
        """查询字典类型列表（分页）.

        参数:
            include_disabled: 是否包含禁用状态的字典类型。
            offset: SQL OFFSET。
            limit: SQL LIMIT。

        返回:
            (字典类型列表, 总数) 元组。
        """

    # ── 字典项 CRUD — SPEC 17.2 ───────────────────────────────────────

    @abstractmethod
    async def add_dict_item(self, item: DictItem) -> None:
        """添加新字典项到当前事务.

        稳定值冲突时由数据库唯一约束拦截。
        """

    @abstractmethod
    async def get_dict_item_by_id(self, item_id: UUID) -> DictItem | None:
        """按 ID 查询字典项，返回领域实体或 None。"""

    @abstractmethod
    async def get_dict_item_by_type_value(
        self,
        dict_type_id: UUID,
        value: str,
    ) -> DictItem | None:
        """按字典类型 + 稳定值查询字典项 — 唯一性检查用.

        SPEC 17.2: 稳定值在同一字典类型内唯一。
        """

    @abstractmethod
    async def save_dict_item(self, item: DictItem) -> None:
        """保存字典项变更到当前事务."""

    @abstractmethod
    async def delete_dict_item_by_id(self, item_id: UUID) -> bool:
        """按 ID 物理删除字典项，返回是否删除成功。"""

    @abstractmethod
    async def list_dict_items(
        self,
        dict_type_id: UUID,
        *,
        include_disabled: bool = True,
    ) -> list[DictItem]:
        """查询指定字典类型下的全部字典项.

        返回结果按 ``sort_order`` 升序排列。

        参数:
            dict_type_id: 字典类型 ID。
            include_disabled: 是否包含禁用状态的字典项。
        """


class ReferenceRegistryPort(ABC):
    """字典引用登记 Port — 跨模块公开（SPEC 5.2 / 5.5 / 17.1）.

    SPEC 5.5: "模块依赖只允许指向其他模块的公开 Application Port"。
    业务模块通过此 Port 登记对字典类型的引用，不直接访问字典模块的数据表。

    SPEC 17.1: "已被业务引用的字典类型具有删除保护"。
    基座提供引用登记 Port（业务模块编码 + 资源标识）供业务模块登记引用，
    删除时检查；基座自身无业务引用场景。

    引用登记使用 ``dict_type_code`` + ``module_code`` + ``resource_id``
    复合唯一约束保证幂等——重复登记不产生重复记录。

    SPEC 5.6: 此 Port 实现不自行提交或回滚事务。
    SPEC 5.7: 引用登记与业务数据在同一事务提交。
    """

    @abstractmethod
    async def register_reference(
        self,
        dict_type_code: str,
        module_code: str,
        resource_id: str,
        *,
        created_at: object,
    ) -> None:
        """登记字典类型引用 — 幂等.

        SPEC 17.1: 业务模块在持久化稳定值时登记对字典类型的引用。
        重复登记同一 (dict_type_code, module_code, resource_id) 不产生
        重复记录（复合唯一约束保证幂等）。

        参数:
            dict_type_code: 被引用的字典类型稳定编码。
            module_code:    引用方业务模块编码。
            resource_id:    引用方资源标识。
            created_at:     登记时间（UTC datetime）。
        """

    @abstractmethod
    async def release_reference(
        self,
        dict_type_code: str,
        module_code: str,
        resource_id: str,
    ) -> None:
        """释放字典类型引用 — 幂等.

        SPEC 17.1: 业务模块在不再引用字典类型时释放登记。
        引用不存在时不产生错误（幂等）。

        参数:
            dict_type_code: 被引用的字典类型稳定编码。
            module_code:    引用方业务模块编码。
            resource_id:    引用方资源标识。
        """

    @abstractmethod
    async def count_references(self, dict_type_code: str) -> int:
        """查询字典类型的引用登记数量 — 删除保护用.

        SPEC 17.1: 删除字典类型时检查是否存在引用登记。
        引用数量大于 0 时拒绝删除。

        参数:
            dict_type_code: 字典类型稳定编码。

        返回:
            引用登记数量。
        """
