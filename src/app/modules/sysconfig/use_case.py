"""系统配置 Use Case 与统一读取服务 — SPEC 5.2 / 5.6 / 5.7 / 16.1 / 16.2 / 18.2.

Application 层应用服务。

SPEC 5.6 事务管理:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。

SPEC 5.7 审计:
  - 配置变更通过 AuditPort 写审计，并与业务事务共同提交。

SPEC 16.1 配置项管理:
  - 创建/查询/更新/启用禁用/分组管理。
  - 配置键在分组内唯一。
  - 配置值保存时类型校验。
  - 敏感配置加密存储且 API 不回显明文。
  - 核心安全配置不可被普通后台覆盖。

SPEC 16.2 配置读取:
  - 统一读取服务按模块声明的键白名单注入。
  - 越键读取报错。
  - 默认直读 PostgreSQL，不启用缓存。
  - 不提供隐式全局配置读取对象。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.modules.audit.diff import FieldWhitelist, generate_diff
from app.modules.audit.models import AuditEntry
from app.modules.sysconfig.adapter import SqlAlchemyConfigRepository
from app.modules.sysconfig.errors import (
    ConfigAlreadyActiveError,
    ConfigAlreadyDisabledError,
    ConfigDuplicateKeyError,
    ConfigKeyNotDeclaredError,
    ConfigNotFoundError,
    ConfigValueTypeMismatchError,
    CoreSecurityConfigProtectedError,
)
from app.modules.sysconfig.models import ConfigItem, ConfigStatus, ConfigType
from app.modules.sysconfig.schemas import SENSITIVE_MASK

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.context import UseCaseContext
    from app.application.ports import Clock, IdGenerator
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.models import ChangeDiff
    from app.modules.audit.port import AuditPort
    from app.modules.sysconfig.crypto import ConfigEncryptionService
    from app.modules.sysconfig.port import ConfigRepository
    from app.modules.sysconfig.schemas import (
        ConfigCreateRequest,
        ConfigUpdateRequest,
    )


# ── 审计字段白名单 — SPEC 18.2 ──────────────────────────────────────────────
#
# SPEC 18.2: 审计差异使用字段白名单生成。
# 配置值本身不进入差异（可能包含敏感数据）。
# value_type 不含敏感片段，可安全审计。

CONFIG_FIELD_WHITELIST = FieldWhitelist(
    module="sysconfig",
    resource_type="config",
    fields=frozenset(
        {
            "group",
            "key",
            "value_type",
            "is_sensitive",
            "is_core_security",
            "description",
            "status",
        },
    ),
)


# ── 值类型校验辅助 — SPEC 16.1 ──────────────────────────────────────────────


def validate_config_value(value_type: ConfigType, raw: str) -> None:
    """按声明类型校验配置值 — SPEC 16.1.

    SPEC 16.1: "配置值在保存时执行类型校验"。
    非法类型值抛出 ``ConfigValueTypeMismatchError``（参数错误）。

    参数:
        value_type: 声明的值类型。
        raw:        待校验的原始字符串值。

    抛出:
        ConfigValueTypeMismatchError: 值无法按声明类型解析。
    """

    if value_type == ConfigType.STRING:
        return  # 任何非空字符串均合法
    if value_type == ConfigType.INT:
        try:
            int(raw)
        except ValueError:
            raise ConfigValueTypeMismatchError(
                f"配置值 {raw!r} 无法解析为 int 类型",
            ) from None
        return
    if value_type == ConfigType.BOOL:
        if raw not in ("true", "false"):
            raise ConfigValueTypeMismatchError(
                f"配置值 {raw!r} 无法解析为 bool 类型（需 'true' 或 'false'）",
            ) from None
        return
    if value_type == ConfigType.JSON:
        try:
            json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise ConfigValueTypeMismatchError(
                "配置值无法解析为 json 类型",
            ) from None
        return


def _parse_typed_value(
    value_type: ConfigType,
    raw: str,
) -> str | int | bool | dict[str, object]:
    """将原始字符串值按声明类型转换为 Python 类型 — 供 ConfigReadService 使用."""

    if value_type == ConfigType.INT:
        return int(raw)
    if value_type == ConfigType.BOOL:
        return raw == "true"
    if value_type == ConfigType.JSON:
        parsed: object = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        raise ConfigValueTypeMismatchError(
            "json 类型配置值顶层必须为 JSON 对象",
        )
    return raw


def _mask_response_value(item: ConfigItem) -> str:
    """返回 API 响应用的配置值 — 敏感配置掩码 — SPEC 16.1."""

    if item.is_sensitive:
        return SENSITIVE_MASK
    return item.stored_value


class ConfigUseCase:
    """系统配置 Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    SPEC 5.7: 审计通过 ``AuditPort`` 显式调用，与业务事务共同提交。

    构造参数:
        uow_factory:        UoW 工厂。
        clock:              时钟 Port。
        id_generator:       标识生成器 Port。
        audit_factory:      审计 Port 工厂。
        encryption_service: 敏感配置加密服务。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        audit_factory: Callable[[AsyncSession], AuditPort],
        encryption_service: ConfigEncryptionService,
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._audit_factory = audit_factory
        self._encryption = encryption_service

    def _create_repo(self, session: AsyncSession) -> ConfigRepository:
        """从 session 构造 Repository Adapter — SPEC 5.6."""

        return SqlAlchemyConfigRepository(session)

    def _create_audit(self, session: AsyncSession) -> AuditPort:
        """从 session 构造审计 Port — SPEC 5.7 / 5.2."""

        return self._audit_factory(session)

    def _make_audit_entry(
        self,
        ctx: UseCaseContext,
        *,
        action: str,
        resource_id: str | None,
        resource_display_name: str | None,
        diff: ChangeDiff | None = None,
    ) -> AuditEntry:
        """构造操作审计条目 — SPEC 18.2 / 5.7."""

        return AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="sysconfig",
            action=action,
            resource_type="config",
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result="success",
            request_id=ctx.request_id or None,
            diff=diff,
            occurred_at=self._clock.now(),
        )

    @staticmethod
    def _config_state(item: ConfigItem) -> dict[str, str | int | None | bool]:
        """提取审计白名单字段状态 — SPEC 18.2."""

        return {
            "group": item.group,
            "key": item.key,
            "value_type": item.value_type.value,
            "is_sensitive": item.is_sensitive,
            "is_core_security": item.is_core_security,
            "description": item.description,
            "status": item.status.value,
        }

    # ── 配置项管理 ──────────────────────────────────────────────────────

    async def create_config(
        self,
        ctx: UseCaseContext,
        request: ConfigCreateRequest,
    ) -> dict[str, object]:
        """创建配置项 — SPEC 16.1.

        校验:
          1. 配置值按声明类型校验（SPEC 16.1）。
          2. 配置键在分组内唯一（SPEC 16.1）。
        加密: 敏感配置值加密后存储（SPEC 16.1 / 23.2）。
        """

        now = self._clock.now()
        config_id = self._id_generator.generate_id()
        value_type = ConfigType(request.value_type)

        # SPEC 16.1: 保存时类型校验
        validate_config_value(value_type, request.value)

        # 加密敏感配置值
        stored_value = (
            self._encryption.encrypt(request.value)
            if request.is_sensitive
            else request.value
        )

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            # SPEC 16.1: 分组内唯一
            existing = await repo.get_by_group_key(request.group, request.key)
            if existing is not None:
                raise ConfigDuplicateKeyError(
                    f"配置键 '{request.key}' 在分组 '{request.group}' 中已存在",
                )

            item = ConfigItem(
                id=config_id,
                group=request.group,
                key=request.key,
                value_type=value_type,
                stored_value=stored_value,
                is_sensitive=request.is_sensitive,
                is_core_security=request.is_core_security,
                description=request.description,
                status=ConfigStatus.ACTIVE,
                created_at=now,
                updated_at=now,
                created_by=ctx.actor_id,
                updated_by=ctx.actor_id,
            )
            await repo.add(item)

            diff = generate_diff(
                CONFIG_FIELD_WHITELIST,
                before=None,
                after=self._config_state(item),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="sysconfig.create",
                    resource_id=str(config_id),
                    resource_display_name=f"{item.group}.{item.key}",
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(item)

    async def get_config(
        self,
        ctx: UseCaseContext,
        config_id: UUID,
    ) -> dict[str, object]:
        """查询配置项详情 — SPEC 16.1.

        SPEC 16.1: 敏感配置 API 响应不回显明文（掩码）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            item = await repo.get_by_id(config_id)
            if item is None:
                raise ConfigNotFoundError(str(config_id))
            return _to_response_dict(item)

    async def list_configs(
        self,
        ctx: UseCaseContext,
        *,
        group: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, object]]:
        """查询配置项列表 — SPEC 16.1 按分组管理."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            items = await repo.list_items(
                group=group,
                include_disabled=include_disabled,
            )
            return [_to_response_dict(item) for item in items]

    async def list_groups(
        self,
        ctx: UseCaseContext,
    ) -> list[dict[str, object]]:
        """查询全部配置分组 — SPEC 16.1 按分组管理."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            groups = await repo.list_groups()
            result: list[dict[str, object]] = []
            for g in groups:
                items = await repo.list_items(group=g)
                result.append({"group": g, "item_count": len(items)})
            return result

    async def update_config(
        self,
        ctx: UseCaseContext,
        config_id: UUID,
        request: ConfigUpdateRequest,
    ) -> dict[str, object]:
        """更新配置项 — SPEC 16.1.

        SPEC 16.1: "核心安全配置不得由普通后台配置随意覆盖"。
        核心安全配置不可通过此端点更新。

        校验:
          1. 核心安全配置保护。
          2. 配置值按声明类型校验。
        """

        now = self._clock.now()

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_by_id(config_id)
            if existing is None:
                raise ConfigNotFoundError(str(config_id))

            # SPEC 16.1: 核心安全配置保护
            if existing.is_core_security:
                raise CoreSecurityConfigProtectedError(
                    f"配置项 '{existing.group}.{existing.key}' "
                    f"标记为核心安全配置，不可通过普通后台配置覆盖",
                )

            # SPEC 16.1: 保存时类型校验
            validate_config_value(existing.value_type, request.value)

            before_state = self._config_state(existing)

            # 加密敏感配置值
            stored_value = (
                self._encryption.encrypt(request.value)
                if existing.is_sensitive
                else request.value
            )

            updated = ConfigItem(
                id=existing.id,
                group=existing.group,
                key=existing.key,
                value_type=existing.value_type,
                stored_value=stored_value,
                is_sensitive=existing.is_sensitive,
                is_core_security=existing.is_core_security,
                description=request.description,
                status=existing.status,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            after_state = self._config_state(updated)
            diff = generate_diff(
                CONFIG_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="sysconfig.update",
                    resource_id=str(config_id),
                    resource_display_name=f"{updated.group}.{updated.key}",
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def enable_config(
        self,
        ctx: UseCaseContext,
        config_id: UUID,
    ) -> dict[str, object]:
        """启用配置项 — SPEC 16.1.

        SPEC 16.1: 核心安全配置保护——不可通过普通后台启用/禁用。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_by_id(config_id)
            if existing is None:
                raise ConfigNotFoundError(str(config_id))

            if existing.is_core_security:
                raise CoreSecurityConfigProtectedError(
                    f"配置项 '{existing.group}.{existing.key}' "
                    f"标记为核心安全配置，不可通过普通后台配置覆盖",
                )

            if existing.status == ConfigStatus.ACTIVE:
                raise ConfigAlreadyActiveError(str(config_id))

            before_state = self._config_state(existing)

            updated = ConfigItem(
                id=existing.id,
                group=existing.group,
                key=existing.key,
                value_type=existing.value_type,
                stored_value=existing.stored_value,
                is_sensitive=existing.is_sensitive,
                is_core_security=existing.is_core_security,
                description=existing.description,
                status=ConfigStatus.ACTIVE,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            after_state = self._config_state(updated)
            diff = generate_diff(
                CONFIG_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="sysconfig.enable",
                    resource_id=str(config_id),
                    resource_display_name=f"{updated.group}.{updated.key}",
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)

    async def disable_config(
        self,
        ctx: UseCaseContext,
        config_id: UUID,
    ) -> dict[str, object]:
        """禁用配置项 — SPEC 16.1.

        SPEC 16.1: 核心安全配置保护——不可通过普通后台启用/禁用。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)

            existing = await repo.get_by_id(config_id)
            if existing is None:
                raise ConfigNotFoundError(str(config_id))

            if existing.is_core_security:
                raise CoreSecurityConfigProtectedError(
                    f"配置项 '{existing.group}.{existing.key}' "
                    f"标记为核心安全配置，不可通过普通后台配置覆盖",
                )

            if existing.status == ConfigStatus.DISABLED:
                raise ConfigAlreadyDisabledError(str(config_id))

            before_state = self._config_state(existing)

            updated = ConfigItem(
                id=existing.id,
                group=existing.group,
                key=existing.key,
                value_type=existing.value_type,
                stored_value=existing.stored_value,
                is_sensitive=existing.is_sensitive,
                is_core_security=existing.is_core_security,
                description=existing.description,
                status=ConfigStatus.DISABLED,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            after_state = self._config_state(updated)
            diff = generate_diff(
                CONFIG_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="sysconfig.disable",
                    resource_id=str(config_id),
                    resource_display_name=f"{updated.group}.{updated.key}",
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response_dict(updated)


# ── 统一配置读取服务 — SPEC 16.2 ────────────────────────────────────────────


class ConfigReadService:
    """统一系统配置读取服务 — SPEC 16.2.

    SPEC 16.2:
      - "提供统一的系统配置读取服务"。
      - "业务模块只读取自己声明依赖的配置"。
      - "不提供可以在任意位置随意读取任意键值的隐式全局配置对象"。
      - "默认可直接从 PostgreSQL 读取"。

    每个需要读取系统配置的模块在构造时声明其依赖的配置键白名单。
    越键读取（请求未声明的键）报 ``ConfigKeyNotDeclaredError``。

    SPEC nonGoals: 默认不启用进程内缓存——每次调用直接从 PostgreSQL 读取。

    使用方式::

        service = ConfigReadService(
            uow_factory=uow_factory,
            encryption_service=encryption,
            declared_keys=frozenset({("app", "site_name"), ("app", "max_upload")}),
        )
        site_name = await service.read("app", "site_name")
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        encryption_service: ConfigEncryptionService,
        declared_keys: frozenset[tuple[str, str]],
    ) -> None:
        """初始化配置读取服务.

        参数:
            uow_factory:        UoW 工厂——每次读取创建独立 UoW。
            encryption_service: 敏感配置加密服务（解密用）。
            declared_keys:      模块声明依赖的配置键白名单——(group, key) 集合。
        """

        self._uow_factory = uow_factory
        self._encryption = encryption_service
        self._declared_keys = declared_keys

    async def read(self, group: str, key: str) -> str | int | bool | dict[str, object]:
        """读取配置值（按声明类型返回）— SPEC 16.2.

        SPEC 16.2: "业务模块只读取自己声明依赖的配置"。
        越键读取（键不在声明白名单中）报 ``ConfigKeyNotDeclaredError``。

        参数:
            group: 配置分组。
            key:   配置键。

        返回:
            按声明类型转换后的值（str / int / bool / dict）。

        抛出:
            ConfigKeyNotDeclaredError: 键不在声明白名单中。
            ConfigNotFoundError:       配置项不存在或已禁用。
        """

        if (group, key) not in self._declared_keys:
            raise ConfigKeyNotDeclaredError(
                f"配置键 '{group}.{key}' 不在当前模块声明的配置依赖白名单中",
            )

        async with self._uow_factory() as uow:
            repo = SqlAlchemyConfigRepository(uow.session)
            item = await repo.get_by_group_key(group, key)
            if item is None:
                raise ConfigNotFoundError(f"{group}.{key}")
            if item.status != ConfigStatus.ACTIVE:
                raise ConfigNotFoundError(
                    f"配置项 '{group}.{key}' 已禁用",
                )

            # SPEC 16.1 / 23.2: 敏感配置解密后返回明文
            raw_value = item.stored_value
            if item.is_sensitive:
                raw_value = self._encryption.decrypt(raw_value)

            return _parse_typed_value(item.value_type, raw_value)


# ── 响应转换辅助 ──────────────────────────────────────────────────────────


def _to_response_dict(item: ConfigItem) -> dict[str, object]:
    """配置项领域实体 → 响应字典.

    SPEC 16.1: 敏感配置 API 响应不回显明文（掩码）。
    """

    return {
        "id": item.id,
        "group": item.group,
        "key": item.key,
        "value_type": item.value_type.value,
        "value": _mask_response_value(item),
        "is_sensitive": item.is_sensitive,
        "is_core_security": item.is_core_security,
        "description": item.description,
        "status": item.status.value,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
