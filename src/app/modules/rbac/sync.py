"""权限点目录同步逻辑 — SPEC 25.2 / 13.1.

SPEC 25.2:
  - ``auth sync-permissions`` 幂等同步 G2 启用模块声明的权限点。
  - 权限同步默认只新增和更新，不自动删除代码中已移除但仍被角色引用的权限点。
  - 孤立权限点必须报告并由显式确认命令清理。

SPEC 13.1: 权限点编码小写多段（如 ``system:user:read``），
来自各模块 ``ModuleDefinition`` 声明，启动时装配为目录。

此模块从模块清单收集所有声明的权限编码，与数据库中的 ``rbac_permissions``
目录表进行幂等同步。

同步规则:
  1. 声明但不在数据库中的权限点 → 新增。
  2. 声明且已在数据库中的权限点 → 更新（display_name、module_code）。
  3. 在数据库中但不再被任何模块声明的权限点 → 孤立权限点，只报告不删除。
  4. ``--clean-orphans --confirm`` → 删除孤立权限点（显式确认）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.application.ports import SystemClock, UuidGenerator
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.rbac.adapter import SqlAlchemyRbacRepository
from app.modules.rbac.models import Permission

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class SyncResult:
    """权限同步结果 — SPEC 25.2.

    属性:
        added:         新增的权限编码列表。
        updated:       更新的权限编码列表。
        orphaned:      孤立权限编码列表（在数据库中但不再被声明）。
        cleaned:       已清理的孤立权限编码列表（仅 ``--clean-orphans`` 时）。
        total_in_db:   同步后数据库中的权限点总数。
    """

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)
    total_in_db: int = 0


async def sync_permissions(
    engine: AsyncEngine,
    *,
    declared_permissions: dict[str, str],
    clean_orphans: bool = False,
) -> SyncResult:
    """执行权限点目录幂等同步 — SPEC 25.2.

    参数:
        engine:               数据库引擎。
        declared_permissions: 权限编码 → 声明模块编码的映射。
        clean_orphans:        是否清理孤立权限点（需调用方确保显式确认）。

    返回:
        同步结果。
    """

    clock = SystemClock()
    id_gen = UuidGenerator()

    added: list[str] = []
    updated: list[str] = []
    orphaned: list[str] = []
    cleaned: list[str] = []

    uow = SqlAlchemyUnitOfWork(engine)
    async with uow:
        repo = SqlAlchemyRbacRepository(uow.session)
        all_perms = await repo.list_all_permissions()

        # 构建现有权限编码 → Permission 映射
        existing_map: dict[str, Permission] = {p.code: p for p in all_perms}

        # 1. 新增和更新
        for code, module_code in declared_permissions.items():
            if code not in existing_map:
                perm = Permission(
                    id=id_gen.generate_id(),
                    code=code,
                    display_name=code,
                    description=None,
                    module_code=module_code,
                    is_active=True,
                    created_at=clock.now(),
                    updated_at=clock.now(),
                )
                await repo.add_permission(perm)
                added.append(code)
            else:
                existing = existing_map[code]
                if existing.module_code != module_code:
                    perm = Permission(
                        id=existing.id,
                        code=existing.code,
                        display_name=existing.display_name,
                        description=existing.description,
                        module_code=module_code,
                        is_active=existing.is_active,
                        created_at=existing.created_at,
                        updated_at=clock.now(),
                    )
                    await repo.update_permission(perm)
                    updated.append(code)

        # 2. 检测孤立权限点 — SPEC 25.2
        existing_codes = set(existing_map.keys())
        declared_codes = set(declared_permissions.keys())
        orphaned_codes = existing_codes - declared_codes
        orphaned = sorted(orphaned_codes)

        # 3. 清理孤立权限点（仅 clean_orphans=True）
        if clean_orphans and orphaned_codes:
            orphan_ids: set[UUID] = {existing_map[code].id for code in orphaned_codes}
            await repo.delete_permissions_by_ids(orphan_ids)
            cleaned = sorted(orphaned_codes)

        await uow.commit()

        # 重新计算总数
        final_perms = await repo.list_all_permissions()

    return SyncResult(
        added=sorted(added),
        updated=sorted(updated),
        orphaned=orphaned,
        cleaned=cleaned,
        total_in_db=len(final_perms),
    )


def collect_declared_permissions() -> dict[str, str]:
    """从模块清单收集所有声明的权限编码 — SPEC 25.2.

    返回:
        权限编码 → 声明此权限的模块编码映射。
        如果同一权限编码被多个模块声明，后注册的模块覆盖前者。
    """

    from app.composition.modules import get_module_manifest

    declared: dict[str, str] = {}
    for module_def in get_module_manifest():
        for perm_code in module_def.permission_codes:
            declared[perm_code] = module_def.code
    return declared
