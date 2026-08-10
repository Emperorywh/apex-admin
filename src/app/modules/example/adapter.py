"""示例 Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``ExampleItemRepository`` Port。
Adapter 在内部将 ORM 模型与领域实体互转，确保内层不感知 ORM 类型
（SPEC 5.2: "DTO、API Schema、领域对象和 ORM 模型职责分离"）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.api.pagination import SortField, SortOrder
from app.core.errors.exceptions import UniqueViolationError
from app.infrastructure.db.exceptions import translate_db_exception
from app.modules.example.errors import (
    ExampleItemConflictError,
    ExampleItemNotFoundError,
)
from app.modules.example.models import ExampleItem
from app.modules.example.orm import ExampleItemORM
from app.modules.example.port import ExampleItemRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyExampleItemRepository(ExampleItemRepository):
    """SQLAlchemy 异步 Repository Adapter — 实现 ``ExampleItemRepository`` Port.

    由 Composition Root（或 Use Case 内部）使用当前 UoW 的 ``AsyncSession`` 构造。
    Adapter 方法返回领域实体 ``ExampleItem``，不是 ORM 模型，
    实现 ORM 类型不泄漏（SPEC 5.2 / 8.1）。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession.

        参数:
            session: 当前 UoW 拥有的 AsyncSession（SPEC 5.6）。
        """

        self._session = session

    async def add(self, item: ExampleItem) -> None:
        """添加新条目到当前事务.

        SPEC 8.3: "唯一性规则优先由数据库唯一约束保证"。
        名称冲突时由数据库唯一约束拦截，翻译为 ``ExampleItemConflictError``。
        """

        orm = ExampleItemORM(
            id=item.id,
            name=item.name,
            description=item.description,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                raise ExampleItemConflictError(
                    f"条目名称 '{item.name}' 已存在",
                ) from exc
            raise

    async def get_by_id(self, item_id: UUID) -> ExampleItem | None:
        """按 ID 查询条目，返回领域实体或 None。"""

        stmt = select(ExampleItemORM).where(ExampleItemORM.id == item_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _to_domain(orm) if orm else None

    async def list_items(
        self,
        *,
        offset: int,
        limit: int,
        sort_fields: list[SortField],
    ) -> tuple[list[ExampleItem], int]:
        """分页查询条目列表.

        SPEC 9.4: 排序字段已通过白名单校验，直接用于构建 ORDER BY。
        """

        # 总数查询
        count_stmt = select(func.count()).select_from(ExampleItemORM)
        total = (await self._session.execute(count_stmt)).scalar() or 0

        # 数据查询
        stmt = select(ExampleItemORM).offset(offset).limit(limit)
        for sf in sort_fields:
            col = getattr(ExampleItemORM, sf.name)
            stmt = stmt.order_by(
                col.desc() if sf.order == SortOrder.DESC else col.asc(),
            )
        result = await self._session.execute(stmt)
        items = [_to_domain(orm) for orm in result.scalars().all()]
        return items, int(total)

    async def save(self, item: ExampleItem) -> None:
        """保存条目变更.

        SPEC 8.4: 名称冲突时由数据库唯一约束拦截。
        """

        stmt = select(ExampleItemORM).where(ExampleItemORM.id == item.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            raise ExampleItemNotFoundError(str(item.id))

        orm.name = item.name
        orm.description = item.description
        orm.updated_at = item.updated_at
        try:
            await self._session.flush()
        except IntegrityError as exc:
            translated = translate_db_exception(exc)
            if isinstance(translated, UniqueViolationError):
                raise ExampleItemConflictError(
                    f"条目名称 '{item.name}' 已存在",
                ) from exc
            raise

    async def delete_by_id(self, item_id: UUID) -> bool:
        """按 ID 删除条目，返回是否删除成功。"""

        stmt = select(ExampleItemORM).where(ExampleItemORM.id == item_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True


def _to_domain(orm: ExampleItemORM) -> ExampleItem:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return ExampleItem(
        id=orm.id,
        name=orm.name,
        description=orm.description,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )
