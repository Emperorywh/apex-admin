"""系统配置 Repository Adapter — Infrastructure 层实现（SPEC 5.2 / 5.6）.

SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露 SQLAlchemy 类型"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Adapter 接收 ``AsyncSession``，实现 ``ConfigRepository``。
Adapter 在内部将 ORM 模型与领域实体互转，确保内层不感知 ORM 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import distinct, select

from app.modules.sysconfig.models import ConfigItem, ConfigStatus, ConfigType
from app.modules.sysconfig.orm import SysConfigItemORM
from app.modules.sysconfig.port import ConfigRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyConfigRepository(ConfigRepository):
    """SQLAlchemy 异步系统配置 Repository Adapter."""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Adapter，绑定当前事务的 AsyncSession."""

        self._session = session

    async def add(self, item: ConfigItem) -> None:
        """添加新配置项到当前事务."""

        orm = _item_to_orm(item)
        self._session.add(orm)
        await self._session.flush()

    async def get_by_id(self, config_id: UUID) -> ConfigItem | None:
        """按 ID 查询配置项."""

        stmt = select(SysConfigItemORM).where(SysConfigItemORM.id == config_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_item(orm) if orm else None

    async def get_by_group_key(
        self,
        group: str,
        key: str,
    ) -> ConfigItem | None:
        """按分组+键查询配置项."""

        stmt = select(SysConfigItemORM).where(
            SysConfigItemORM.group == group,
            SysConfigItemORM.key == key,
        )
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return _orm_to_item(orm) if orm else None

    async def save(self, item: ConfigItem) -> None:
        """保存配置项变更."""

        stmt = select(SysConfigItemORM).where(SysConfigItemORM.id == item.id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            from app.modules.sysconfig.errors import ConfigNotFoundError

            raise ConfigNotFoundError(str(item.id))

        orm.group = item.group
        orm.key = item.key
        orm.value_type = item.value_type.value
        orm.stored_value = item.stored_value
        orm.is_sensitive = item.is_sensitive
        orm.is_core_security = item.is_core_security
        orm.description = item.description
        orm.status = item.status.value
        orm.updated_at = item.updated_at
        orm.updated_by = item.updated_by
        await self._session.flush()

    async def list_items(
        self,
        *,
        group: str | None = None,
        include_disabled: bool = True,
    ) -> list[ConfigItem]:
        """查询配置项列表."""

        stmt = select(SysConfigItemORM).order_by(
            SysConfigItemORM.group,
            SysConfigItemORM.key,
        )
        if group is not None:
            stmt = stmt.where(SysConfigItemORM.group == group)
        if not include_disabled:
            stmt = stmt.where(
                SysConfigItemORM.status == ConfigStatus.ACTIVE.value,
            )
        result = await self._session.execute(stmt)
        return [_orm_to_item(orm) for orm in result.scalars().all()]

    async def list_groups(self) -> list[str]:
        """查询全部配置分组（去重）."""

        stmt = select(distinct(SysConfigItemORM.group)).order_by(SysConfigItemORM.group)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_sensitive_items(self) -> list[ConfigItem]:
        """查询全部敏感配置项 — re-encrypt 用."""

        stmt = (
            select(SysConfigItemORM)
            .where(SysConfigItemORM.is_sensitive.is_(True))
            .order_by(SysConfigItemORM.group, SysConfigItemORM.key)
        )
        result = await self._session.execute(stmt)
        return [_orm_to_item(orm) for orm in result.scalars().all()]


# ── ORM ↔ 领域实体转换 ──────────────────────────────────────────────────────


def _orm_to_item(orm: SysConfigItemORM) -> ConfigItem:
    """ORM 模型 → 领域实体转换 — SPEC 5.2 职责分离."""

    return ConfigItem(
        id=orm.id,
        group=orm.group,
        key=orm.key,
        value_type=ConfigType(orm.value_type),
        stored_value=orm.stored_value,
        is_sensitive=orm.is_sensitive,
        is_core_security=orm.is_core_security,
        description=orm.description,
        status=ConfigStatus(orm.status),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        created_by=orm.created_by,
        updated_by=orm.updated_by,
    )


def _item_to_orm(item: ConfigItem) -> SysConfigItemORM:
    """领域实体 → ORM 模型转换."""

    return SysConfigItemORM(
        id=item.id,
        group=item.group,
        key=item.key,
        value_type=item.value_type.value,
        stored_value=item.stored_value,
        is_sensitive=item.is_sensitive,
        is_core_security=item.is_core_security,
        description=item.description,
        status=item.status.value,
        created_at=item.created_at,
        updated_at=item.updated_at,
        created_by=item.created_by,
        updated_by=item.updated_by,
    )
