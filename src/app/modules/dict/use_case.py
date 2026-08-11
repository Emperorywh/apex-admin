"""数据字典 Use Case — Application 层应用服务.

SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

SPEC 17.1 字典类型:
  - 创建/查询/更新/启用禁用/删除。
  - 字典编码保持稳定和唯一。
  - 已被业务引用的字典类型具有删除保护。

SPEC 17.2 字典项:
  - 创建/查询/更新/启用禁用/删除。
  - 支持显示文本、稳定值、排序和扩展元数据。
  - 字典项变更具有审计记录。
  - 业务数据持久化稳定值，而不是展示文本。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.audit.diff import FieldWhitelist, generate_diff
from app.modules.audit.models import AuditEntry
from app.modules.dict.adapter import (
    SqlAlchemyDictRepository,
    SqlAlchemyReferenceRegistry,
)
from app.modules.dict.errors import (
    DictItemAlreadyActiveError,
    DictItemAlreadyDisabledError,
    DictItemDuplicateValueError,
    DictItemNotFoundError,
    DictTypeAlreadyActiveError,
    DictTypeAlreadyDisabledError,
    DictTypeDisabledError,
    DictTypeDuplicateCodeError,
    DictTypeNotFoundError,
    DictTypeReferencedError,
)
from app.modules.dict.models import (
    DictItem,
    DictItemStatus,
    DictType,
    DictTypeStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.context import UseCaseContext
    from app.application.ports import Clock, IdGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.models import ChangeDiff
    from app.modules.audit.port import AuditPort
    from app.modules.dict.port import DictRepository
    from app.modules.dict.schemas import (
        DictItemCreateRequest,
        DictItemUpdateRequest,
        DictTypeCreateRequest,
        DictTypeUpdateRequest,
    )


# ── 审计字段白名单 — SPEC 18.2 ──────────────────────────────────────────────
#
# SPEC 18.2: 审计差异使用字段白名单生成。
# 字典项的 value 是业务持久化值，可安全审计。

DICT_TYPE_FIELD_WHITELIST = FieldWhitelist(
    module="dict",
    resource_type="dict_type",
    fields=frozenset(
        {
            "code",
            "name",
            "description",
            "status",
        },
    ),
)

DICT_ITEM_FIELD_WHITELIST = FieldWhitelist(
    module="dict",
    resource_type="dict_item",
    fields=frozenset(
        {
            "dict_type_id",
            "label",
            "value",
            "sort_order",
            "description",
            "status",
        },
    ),
)


class DictUseCase:
    """数据字典 Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

    构造参数:
        uow_factory:   UoW 工厂。
        clock:         时钟 Port。
        id_generator:  标识生成器 Port。
        audit_factory: 审计 Port 工厂。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        audit_factory: Callable[[AsyncSession], AuditPort],
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._audit_factory = audit_factory

    def _create_repo(self, session: AsyncSession) -> DictRepository:
        """从 session 构造 Repository Adapter — SPEC 5.6."""

        return SqlAlchemyDictRepository(session)

    def _create_audit(self, session: AsyncSession) -> AuditPort:
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return self._audit_factory(session)

    def _make_audit_entry(
        self,
        ctx: UseCaseContext,
        *,
        action: str,
        resource_type: str,
        resource_id: str | None,
        resource_display_name: str | None,
        diff: ChangeDiff | None = None,
    ) -> AuditEntry:
        """构造操作审计条目 — SPEC 18.2 / 5.7."""

        return AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="dict",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result="success",
            request_id=ctx.request_id or None,
            diff=diff,
            occurred_at=self._clock.now(),
        )

    # ── 审计字段状态提取 ──────────────────────────────────────────────

    @staticmethod
    def _dict_type_state(dt: DictType) -> dict[str, str | None]:
        """提取字典类型审计白名单字段状态 — SPEC 18.2."""

        return {
            "code": dt.code,
            "name": dt.name,
            "description": dt.description,
            "status": dt.status.value,
        }

    @staticmethod
    def _dict_item_state(item: DictItem) -> dict[str, str | int | None]:
        """提取字典项审计白名单字段状态 — SPEC 18.2."""

        return {
            "dict_type_id": str(item.dict_type_id),
            "label": item.label,
            "value": item.value,
            "sort_order": item.sort_order,
            "description": item.description,
            "status": item.status.value,
        }

    # ════════════════════════════════════════════════════════════════════════
    # 字典类型管理 — SPEC 17.1
    # ════════════════════════════════════════════════════════════════════════

    async def create_dict_type(
        self,
        ctx: UseCaseContext,
        request: DictTypeCreateRequest,
    ) -> dict[str, object]:
        """创建字典类型 — SPEC 17.1.

        校验: 字典编码全局唯一（SPEC 17.1: 字典编码保持稳定和唯一）。
        """

        now = self._clock.now()
        type_id = self._id_generator.generate_id()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_type_by_code(request.code)
            if existing is not None:
                raise DictTypeDuplicateCodeError(
                    f"字典编码 '{request.code}' 已存在",
                )

            dt = DictType(
                id=type_id,
                code=request.code,
                name=request.name,
                description=request.description,
                status=DictTypeStatus.ACTIVE,
                created_at=now,
                updated_at=now,
                created_by=ctx.actor_id,
                updated_by=ctx.actor_id,
            )
            await repo.add_dict_type(dt)

            diff = generate_diff(
                DICT_TYPE_FIELD_WHITELIST,
                before=None,
                after=self._dict_type_state(dt),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.type.create",
                    resource_type="dict_type",
                    resource_id=str(type_id),
                    resource_display_name=dt.code,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_type_to_response(dt)

    async def get_dict_type(
        self,
        ctx: UseCaseContext,
        type_id: UUID,
    ) -> dict[str, object]:
        """查询字典类型详情 — SPEC 17.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            dt = await repo.get_dict_type_by_id(type_id)
            if dt is None:
                raise DictTypeNotFoundError(str(type_id))
            return _dict_type_to_response(dt)

    async def list_dict_types(
        self,
        ctx: UseCaseContext,
        *,
        include_disabled: bool = True,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, object]], int]:
        """查询字典类型列表 — SPEC 17.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            types, total = await repo.list_dict_types(
                include_disabled=include_disabled,
                offset=offset,
                limit=limit,
            )
            return [_dict_type_to_response(dt) for dt in types], total

    async def update_dict_type(
        self,
        ctx: UseCaseContext,
        type_id: UUID,
        request: DictTypeUpdateRequest,
    ) -> dict[str, object]:
        """更新字典类型 — SPEC 17.1.

        编码不可变更（稳定标识）。更新名称和描述。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_type_by_id(type_id)
            if existing is None:
                raise DictTypeNotFoundError(str(type_id))

            before_state = self._dict_type_state(existing)

            updated = DictType(
                id=existing.id,
                code=existing.code,
                name=request.name,
                description=request.description,
                status=existing.status,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_dict_type(updated)

            after_state = self._dict_type_state(updated)
            diff = generate_diff(
                DICT_TYPE_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.type.update",
                    resource_type="dict_type",
                    resource_id=str(type_id),
                    resource_display_name=updated.code,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_type_to_response(updated)

    async def enable_dict_type(
        self,
        ctx: UseCaseContext,
        type_id: UUID,
    ) -> dict[str, object]:
        """启用字典类型 — SPEC 17.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_type_by_id(type_id)
            if existing is None:
                raise DictTypeNotFoundError(str(type_id))

            if existing.status == DictTypeStatus.ACTIVE:
                raise DictTypeAlreadyActiveError(str(type_id))

            before_state = self._dict_type_state(existing)

            updated = DictType(
                id=existing.id,
                code=existing.code,
                name=existing.name,
                description=existing.description,
                status=DictTypeStatus.ACTIVE,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_dict_type(updated)

            after_state = self._dict_type_state(updated)
            diff = generate_diff(
                DICT_TYPE_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.type.enable",
                    resource_type="dict_type",
                    resource_id=str(type_id),
                    resource_display_name=updated.code,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_type_to_response(updated)

    async def disable_dict_type(
        self,
        ctx: UseCaseContext,
        type_id: UUID,
    ) -> dict[str, object]:
        """禁用字典类型 — SPEC 17.1."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_type_by_id(type_id)
            if existing is None:
                raise DictTypeNotFoundError(str(type_id))

            if existing.status == DictTypeStatus.DISABLED:
                raise DictTypeAlreadyDisabledError(str(type_id))

            before_state = self._dict_type_state(existing)

            updated = DictType(
                id=existing.id,
                code=existing.code,
                name=existing.name,
                description=existing.description,
                status=DictTypeStatus.DISABLED,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_dict_type(updated)

            after_state = self._dict_type_state(updated)
            diff = generate_diff(
                DICT_TYPE_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.type.disable",
                    resource_type="dict_type",
                    resource_id=str(type_id),
                    resource_display_name=updated.code,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_type_to_response(updated)

    async def delete_dict_type(
        self,
        ctx: UseCaseContext,
        type_id: UUID,
    ) -> None:
        """删除字典类型 — SPEC 17.1.

        SPEC 17.1: "已被业务引用的字典类型具有删除保护"。
        通过引用登记 Port 检查是否存在引用登记。

        同时删除该类型下的全部字典项（级联删除）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            registry = SqlAlchemyReferenceRegistry(uow.session)

            existing = await repo.get_dict_type_by_id(type_id)
            if existing is None:
                raise DictTypeNotFoundError(str(type_id))

            # SPEC 17.1: 删除保护——检查引用登记
            ref_count = await registry.count_references(existing.code)
            if ref_count > 0:
                raise DictTypeReferencedError(
                    f"字典类型 '{existing.code}' 被 {ref_count} 个业务资源引用，"
                    f"不可删除",
                )

            # 删除全部字典项
            items = await repo.list_dict_items(type_id, include_disabled=True)
            for item in items:
                await repo.delete_dict_item_by_id(item.id)

            await repo.delete_dict_type_by_id(type_id)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.type.delete",
                    resource_type="dict_type",
                    resource_id=str(type_id),
                    resource_display_name=existing.code,
                ),
            )

            await uow.commit()

    # ════════════════════════════════════════════════════════════════════════
    # 字典项管理 — SPEC 17.2
    # ════════════════════════════════════════════════════════════════════════

    async def create_dict_item(
        self,
        ctx: UseCaseContext,
        type_id: UUID,
        request: DictItemCreateRequest,
    ) -> dict[str, object]:
        """创建字典项 — SPEC 17.2.

        SPEC 17.2: 支持显示文本、稳定值、排序和扩展元数据。
        校验: 所属字典类型存在且启用；稳定值在同类内唯一。
        """

        now = self._clock.now()
        item_id = self._id_generator.generate_id()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            dt = await repo.get_dict_type_by_id(type_id)
            if dt is None:
                raise DictTypeNotFoundError(str(type_id))

            if dt.status == DictTypeStatus.DISABLED:
                raise DictTypeDisabledError(
                    f"字典类型 '{dt.code}' 已禁用，不可在其下创建字典项",
                )

            # SPEC 17.2: 稳定值在同类内唯一
            existing = await repo.get_dict_item_by_type_value(type_id, request.value)
            if existing is not None:
                raise DictItemDuplicateValueError(
                    f"字典项稳定值 '{request.value}' 在字典类型 '{dt.code}' 中已存在",
                )

            item = DictItem(
                id=item_id,
                dict_type_id=type_id,
                label=request.label,
                value=request.value,
                sort_order=request.sort_order,
                metadata_=request.metadata,
                description=request.description,
                status=DictItemStatus.ACTIVE,
                created_at=now,
                updated_at=now,
                created_by=ctx.actor_id,
                updated_by=ctx.actor_id,
            )
            await repo.add_dict_item(item)

            diff = generate_diff(
                DICT_ITEM_FIELD_WHITELIST,
                before=None,
                after=self._dict_item_state(item),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.item.create",
                    resource_type="dict_item",
                    resource_id=str(item_id),
                    resource_display_name=f"{dt.code}:{item.value}",
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_item_to_response(item)

    async def get_dict_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
    ) -> dict[str, object]:
        """查询字典项详情 — SPEC 17.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            item = await repo.get_dict_item_by_id(item_id)
            if item is None:
                raise DictItemNotFoundError(str(item_id))
            return _dict_item_to_response(item)

    async def list_dict_items(
        self,
        ctx: UseCaseContext,
        type_id: UUID,
        *,
        include_disabled: bool = True,
    ) -> list[dict[str, object]]:
        """查询指定字典类型下的全部字典项 — SPEC 17.2.

        返回结果按 ``sort_order`` 升序排列。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            # 校验字典类型存在
            dt = await repo.get_dict_type_by_id(type_id)
            if dt is None:
                raise DictTypeNotFoundError(str(type_id))
            items = await repo.list_dict_items(
                type_id,
                include_disabled=include_disabled,
            )
            return [_dict_item_to_response(item) for item in items]

    async def update_dict_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
        request: DictItemUpdateRequest,
    ) -> dict[str, object]:
        """更新字典项 — SPEC 17.2.

        SPEC 17.2: 支持更新显示文本、稳定值、排序和扩展元数据。
        校验: 所属字典类型启用；稳定值在同类内唯一（排除自身）。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_item_by_id(item_id)
            if existing is None:
                raise DictItemNotFoundError(str(item_id))

            dt = await repo.get_dict_type_by_id(existing.dict_type_id)
            if dt is None:
                raise DictTypeNotFoundError(str(existing.dict_type_id))

            if dt.status == DictTypeStatus.DISABLED:
                raise DictTypeDisabledError(
                    f"字典类型 '{dt.code}' 已禁用，不可修改其字典项",
                )

            # SPEC 17.2: 稳定值唯一性检查（排除自身）
            if request.value != existing.value:
                dup = await repo.get_dict_item_by_type_value(
                    existing.dict_type_id,
                    request.value,
                )
                if dup is not None:
                    raise DictItemDuplicateValueError(
                        f"字典项稳定值 '{request.value}' 在字典类型 "
                        f"'{dt.code}' 中已存在",
                    )

            before_state = self._dict_item_state(existing)

            updated = DictItem(
                id=existing.id,
                dict_type_id=existing.dict_type_id,
                label=request.label,
                value=request.value,
                sort_order=request.sort_order,
                metadata_=request.metadata,
                description=request.description,
                status=existing.status,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_dict_item(updated)

            after_state = self._dict_item_state(updated)
            diff = generate_diff(
                DICT_ITEM_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.item.update",
                    resource_type="dict_item",
                    resource_id=str(item_id),
                    resource_display_name=f"{dt.code}:{updated.value}",
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_item_to_response(updated)

    async def enable_dict_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
    ) -> dict[str, object]:
        """启用字典项 — SPEC 17.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_item_by_id(item_id)
            if existing is None:
                raise DictItemNotFoundError(str(item_id))

            if existing.status == DictItemStatus.ACTIVE:
                raise DictItemAlreadyActiveError(str(item_id))

            before_state = self._dict_item_state(existing)

            updated = DictItem(
                id=existing.id,
                dict_type_id=existing.dict_type_id,
                label=existing.label,
                value=existing.value,
                sort_order=existing.sort_order,
                metadata_=existing.metadata_,
                description=existing.description,
                status=DictItemStatus.ACTIVE,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_dict_item(updated)

            after_state = self._dict_item_state(updated)
            diff = generate_diff(
                DICT_ITEM_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.item.enable",
                    resource_type="dict_item",
                    resource_id=str(item_id),
                    resource_display_name=None,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_item_to_response(updated)

    async def disable_dict_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
    ) -> dict[str, object]:
        """禁用字典项 — SPEC 17.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_item_by_id(item_id)
            if existing is None:
                raise DictItemNotFoundError(str(item_id))

            if existing.status == DictItemStatus.DISABLED:
                raise DictItemAlreadyDisabledError(str(item_id))

            before_state = self._dict_item_state(existing)

            updated = DictItem(
                id=existing.id,
                dict_type_id=existing.dict_type_id,
                label=existing.label,
                value=existing.value,
                sort_order=existing.sort_order,
                metadata_=existing.metadata_,
                description=existing.description,
                status=DictItemStatus.DISABLED,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save_dict_item(updated)

            after_state = self._dict_item_state(updated)
            diff = generate_diff(
                DICT_ITEM_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.item.disable",
                    resource_type="dict_item",
                    resource_id=str(item_id),
                    resource_display_name=None,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _dict_item_to_response(updated)

    async def delete_dict_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
    ) -> None:
        """删除字典项 — SPEC 17.2."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_dict_item_by_id(item_id)
            if existing is None:
                raise DictItemNotFoundError(str(item_id))

            await repo.delete_dict_item_by_id(item_id)

            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="dict.item.delete",
                    resource_type="dict_item",
                    resource_id=str(item_id),
                    resource_display_name=existing.value,
                ),
            )

            await uow.commit()


# ── 响应转换辅助 ──────────────────────────────────────────────────────────


def _dict_type_to_response(dt: DictType) -> dict[str, object]:
    """字典类型领域实体 → 响应字典."""

    return {
        "id": dt.id,
        "code": dt.code,
        "name": dt.name,
        "description": dt.description,
        "status": dt.status.value,
        "created_at": dt.created_at,
        "updated_at": dt.updated_at,
    }


def _dict_item_to_response(item: DictItem) -> dict[str, object]:
    """字典项领域实体 → 响应字典."""

    return {
        "id": item.id,
        "dict_type_id": item.dict_type_id,
        "label": item.label,
        "value": item.value,
        "sort_order": item.sort_order,
        "metadata": item.metadata_,
        "description": item.description,
        "status": item.status.value,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
