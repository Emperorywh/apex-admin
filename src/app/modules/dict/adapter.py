"""数据字典 Repository Adapter 与引用登记 Adapter — Infrastructure 层.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``DictRepository`` 和
``ReferenceRegistryPort``。Adapter 在内部将 ORM 模型与领域实体互转，
确保内层不感知 ORM 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.modules.dict.models import (
    DictItem,
    DictItemStatus,
    DictType,
    DictTypeStatus,
)
from app.modules.dict.orm import DictItemORM, DictReferenceORM, DictTypeORM
from app.modules.dict.port import DictRepository, ReferenceRegistryPort

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyDictRepository(DictRepository):
    """SQLAlchemy 异步数据字典 Repository Adapter."""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    # ── 字典类型 CRUD ──────────────────────────────────────────────────

    async def add_dict_type(self, dict_type: DictType) -> None:
        """添加新字典类型到当前事务."""

        orm = _dict_type_to_orm(dict_type)
        self._session.add(orm)
        await self._session.flush()

    async def get_dict_type_by_id(self, dict_type_id: UUID) -> DictType | None:
        """按 ID 查询字典类型."""

        stmt = select(DictTypeORM).where(DictTypeORM.id == dict_type_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_dict_type(orm) if orm else None

    async def get_dict_type_by_code(self, code: str) -> DictType | None:
        """按编码查询字典类型."""

        stmt = select(DictTypeORM).where(DictTypeORM.code == code)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_dict_type(orm) if orm else None

    async def save_dict_type(self, dict_type: DictType) -> None:
        """保存字典类型变更."""

        stmt = select(DictTypeORM).where(DictTypeORM.id == dict_type.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.dict.errors import DictTypeNotFoundError

            raise DictTypeNotFoundError(str(dict_type.id))

        orm.code = dict_type.code
        orm.name = dict_type.name
        orm.description = dict_type.description
        orm.status = dict_type.status.value
        orm.updated_at = dict_type.updated_at
        orm.updated_by = dict_type.updated_by
        await self._session.flush()

    async def delete_dict_type_by_id(self, dict_type_id: UUID) -> bool:
        """按 ID 物理删除字典类型."""

        stmt = select(DictTypeORM).where(DictTypeORM.id == dict_type_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    async def list_dict_types(
        self,
        *,
        include_disabled: bool = True,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[DictType], int]:
        """查询字典类型列表（分页）."""

        base = select(DictTypeORM)
        count_base = select(func.count()).select_from(DictTypeORM)
        if not include_disabled:
            base = base.where(DictTypeORM.status == DictTypeStatus.ACTIVE.value)
            count_base = count_base.where(
                DictTypeORM.status == DictTypeStatus.ACTIVE.value,
            )

        base = base.order_by(DictTypeORM.code).offset(offset).limit(limit)
        result = await self._session.execute(base)
        types = [_orm_to_dict_type(orm) for orm in result.scalars().all()]

        count_result = await self._session.execute(count_base)
        total = count_result.scalar() or 0

        return types, total

    # ── 字典项 CRUD ────────────────────────────────────────────────────

    async def add_dict_item(self, item: DictItem) -> None:
        """添加新字典项到当前事务."""

        orm = _dict_item_to_orm(item)
        self._session.add(orm)
        await self._session.flush()

    async def get_dict_item_by_id(self, item_id: UUID) -> DictItem | None:
        """按 ID 查询字典项."""

        stmt = select(DictItemORM).where(DictItemORM.id == item_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_dict_item(orm) if orm else None

    async def get_dict_item_by_type_value(
        self,
        dict_type_id: UUID,
        value: str,
    ) -> DictItem | None:
        """按字典类型 + 稳定值查询字典项."""

        stmt = select(DictItemORM).where(
            DictItemORM.dict_type_id == dict_type_id,
            DictItemORM.value == value,
        )
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_dict_item(orm) if orm else None

    async def save_dict_item(self, item: DictItem) -> None:
        """保存字典项变更."""

        stmt = select(DictItemORM).where(DictItemORM.id == item.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.dict.errors import DictItemNotFoundError

            raise DictItemNotFoundError(str(item.id))

        orm.dict_type_id = item.dict_type_id
        orm.label = item.label
        orm.value = item.value
        orm.sort_order = item.sort_order
        orm.metadata_ = item.metadata_
        orm.description = item.description
        orm.status = item.status.value
        orm.updated_at = item.updated_at
        orm.updated_by = item.updated_by
        await self._session.flush()

    async def delete_dict_item_by_id(self, item_id: UUID) -> bool:
        """按 ID 物理删除字典项."""

        stmt = select(DictItemORM).where(DictItemORM.id == item_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    async def list_dict_items(
        self,
        dict_type_id: UUID,
        *,
        include_disabled: bool = True,
    ) -> list[DictItem]:
        """查询指定字典类型下的全部字典项（按 sort_order 升序）."""

        stmt = (
            select(DictItemORM)
            .where(DictItemORM.dict_type_id == dict_type_id)
            .order_by(DictItemORM.sort_order, DictItemORM.value)
        )
        if not include_disabled:
            stmt = stmt.where(DictItemORM.status == DictItemStatus.ACTIVE.value)
        result = await self._session.execute(stmt)
        return [_orm_to_dict_item(orm) for orm in result.scalars().all()]


class SqlAlchemyReferenceRegistry(ReferenceRegistryPort):
    """SQLAlchemy 异步字典引用登记 Adapter.

    SPEC 17.1: 业务模块通过此 Adapter 登记对字典类型的引用。
    使用 PostgreSQL ``ON CONFLICT DO NOTHING`` 实现幂等登记。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化引用登记 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def register_reference(
        self,
        dict_type_code: str,
        module_code: str,
        resource_id: str,
        *,
        created_at: object,
    ) -> None:
        """登记字典类型引用 — 幂等（ON CONFLICT DO NOTHING）."""

        from uuid import uuid4

        stmt = pg_insert(DictReferenceORM).values(
            id=uuid4(),
            dict_type_code=dict_type_code,
            module_code=module_code,
            resource_id=resource_id,
            created_at=created_at,
        )
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_dict_references_type_module_resource",
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def release_reference(
        self,
        dict_type_code: str,
        module_code: str,
        resource_id: str,
    ) -> None:
        """释放字典类型引用 — 幂等."""

        stmt = delete(DictReferenceORM).where(
            DictReferenceORM.dict_type_code == dict_type_code,
            DictReferenceORM.module_code == module_code,
            DictReferenceORM.resource_id == resource_id,
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def count_references(self, dict_type_code: str) -> int:
        """查询字典类型的引用登记数量."""

        stmt = (
            select(func.count())
            .select_from(DictReferenceORM)
            .where(DictReferenceORM.dict_type_code == dict_type_code)
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _orm_to_dict_type(orm: DictTypeORM) -> DictType:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return DictType(
        id=orm.id,
        code=orm.code,
        name=orm.name,
        description=orm.description,
        status=DictTypeStatus(orm.status),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _dict_type_to_orm(dict_type: DictType) -> DictTypeORM:
    """领域实体 → ORM 模型转换."""

    return DictTypeORM(
        id=dict_type.id,
        code=dict_type.code,
        name=dict_type.name,
        description=dict_type.description,
        status=dict_type.status.value,
        created_at=dict_type.created_at,
        updated_at=dict_type.updated_at,
        created_by=dict_type.created_by,
        updated_by=dict_type.updated_by,
    )


def _orm_to_dict_item(orm: DictItemORM) -> DictItem:
    """ORM 模型 → 领域实体转换."""

    return DictItem(
        id=orm.id,
        dict_type_id=orm.dict_type_id,
        label=orm.label,
        value=orm.value,
        sort_order=orm.sort_order,
        metadata_=orm.metadata_ or {},
        description=orm.description,
        status=DictItemStatus(orm.status),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _dict_item_to_orm(item: DictItem) -> DictItemORM:
    """领域实体 → ORM 模型转换."""

    return DictItemORM(
        id=item.id,
        dict_type_id=item.dict_type_id,
        label=item.label,
        value=item.value,
        sort_order=item.sort_order,
        metadata_=item.metadata_,
        description=item.description,
        status=item.status.value,
        created_at=item.created_at,
        updated_at=item.updated_at,
        created_by=item.created_by,
        updated_by=item.updated_by,
    )
