"""权限点同步服务（SPEC §25.2）。

幂等同步所有启用模块声明的权限点到 ``permission_points`` 表。
默认只新增和更新，不自动删除——孤立权限点（在 DB 中但不在代码声明中）
只报告不清理（SPEC §25.2）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.modules.contract import ModuleDefinition
from app.modules.rbac.application.port import RbacUnitOfWork


@dataclass(frozen=True)
class SyncResult:
    """权限同步结果（SPEC §25.2）。

    Attributes:
        added: 新增的权限编码集合
        updated: 更新的权限编码集合
        unchanged: 未变化的权限编码集合
        orphans: DB 中存在但代码中未声明的权限编码集合
        orphan_referenced: 孤立且仍被 role_permissions 引用的权限编码集合
    """

    added: frozenset[str] = field(default_factory=frozenset)
    updated: frozenset[str] = field(default_factory=frozenset)
    unchanged: frozenset[str] = field(default_factory=frozenset)
    orphans: frozenset[str] = field(default_factory=frozenset)
    orphan_referenced: frozenset[str] = field(default_factory=frozenset)

    @property
    def total_declared(self) -> int:
        """代码声明的权限点总数。"""
        return len(self.added) + len(self.updated) + len(self.unchanged)


class PermissionSyncService:
    """权限点同步服务（SPEC §25.2）。

    幂等同步启用模块声明的权限点到 ``permission_points`` 表。

    同步语义（SPEC §25.2）：
    - 新增：代码声明但 DB 中不存在的权限点
    - 更新：DB 中存在但描述或模块编码已变化的权限点
    - 不删除：DB 中存在但代码中未声明的权限点标记为孤立，
      只报告不自动删除（SPEC §25.2：默认只新增和更新）
    - 孤立且仍被角色引用的权限点额外标记，需要显式确认命令清理
    """

    def __init__(
        self,
        uow_factory: Callable[[], RbacUnitOfWork],
        modules: list[ModuleDefinition],
    ) -> None:
        """初始化同步服务。

        Args:
            uow_factory: 工作单元工厂
            modules: 已启用模块清单
        """
        self._uow_factory = uow_factory
        self._modules = modules

    async def sync(self, *, current_time: datetime) -> SyncResult:
        """执行权限点同步（SPEC §25.2）。

        1. 从启用模块收集全部声明的权限点
        2. 查询 ``permission_points`` 表现有记录
        3. 对新增和变化的权限点执行 upsert
        4. 检测孤立权限点（DB 中有但代码中未声明）
        5. 标记仍被 ``role_permissions`` 引用的孤立权限点

        Returns:
            :class:`SyncResult` 同步结果
        """
        declared = self._collect_declared_permissions()

        async with self._uow_factory() as uow:
            existing_records = await uow.permission_points.list_all()
            existing_map: dict[str, tuple[str, str]] = {
                code: (desc, mod) for code, desc, mod in existing_records
            }

            added: set[str] = set()
            updated: set[str] = set()
            unchanged: set[str] = set()

            for code, (description, module_code) in sorted(declared.items()):
                if code not in existing_map:
                    added.add(code)
                    await uow.permission_points.upsert(
                        code=code,
                        description=description,
                        module_code=module_code,
                        current_time=current_time,
                    )
                elif existing_map[code] != (description, module_code):
                    updated.add(code)
                    await uow.permission_points.upsert(
                        code=code,
                        description=description,
                        module_code=module_code,
                        current_time=current_time,
                    )
                else:
                    unchanged.add(code)

            # 孤立检测：DB 中有但代码中未声明
            declared_codes = set(declared.keys())
            all_db_codes = set(existing_map.keys())
            orphans = all_db_codes - declared_codes

            # 标记仍被角色引用的孤立权限点（不自动删除，SPEC §25.2）
            referenced_codes = await uow.role_permissions.get_all_referenced_codes()
            orphan_referenced = orphans & referenced_codes

            return SyncResult(
                added=frozenset(added),
                updated=frozenset(updated),
                unchanged=frozenset(unchanged),
                orphans=frozenset(orphans),
                orphan_referenced=frozenset(orphan_referenced),
            )

    def _collect_declared_permissions(self) -> dict[str, tuple[str, str]]:
        """从启用模块收集全部声明的权限点。

        Returns:
            权限编码到 (描述, 模块编码) 的映射
        """
        result: dict[str, tuple[str, str]] = {}
        for module in self._modules:
            for point in module.permission_points:
                result[point.code] = (point.description, module.code)
        return result
