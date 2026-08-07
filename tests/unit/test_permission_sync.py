"""权限点同步服务单元测试（SPEC §25.2）。

验证：
- 首次同步全部声明的权限点
- 连续同步结果一致（幂等）
- 孤立权限点检测
- 孤立且仍被角色引用的权限点不自动删除
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest
from fastapi import APIRouter

from app.modules.contract import (
    ModuleDefinition,
    PermissionPoint,
)
from app.modules.rbac.application.permission_sync import PermissionSyncService
from app.modules.rbac.application.port import (
    PermissionPointRecord,
    RbacUnitOfWork,
    RolePermissionRepository,
    RoleRepository,
    UserRoleRepository,
)
from app.modules.rbac.domain.model import Role

pytestmark = [pytest.mark.unit, pytest.mark.g2]

_NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


class FakePermissionPointRepo(PermissionPointRecord):
    """内存权限点注册表 Repository。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}  # code -> {description, module_code, ...}

    async def upsert(
        self,
        code: str,
        description: str,
        module_code: str,
        current_time: datetime,  # noqa: ARG002
    ) -> bool:
        existing = self._data.get(code)
        if existing is None:
            self._data[code] = {
                "description": description,
                "module_code": module_code,
            }
            return True
        if existing["description"] != description or existing["module_code"] != module_code:
            existing["description"] = description
            existing["module_code"] = module_code
            return True
        return False

    async def list_all(self) -> list[tuple[str, str, str]]:
        return [
            (code, rec["description"], rec["module_code"])
            for code, rec in sorted(self._data.items())
        ]

    async def delete(self, code: str) -> None:
        self._data.pop(code, None)


class FakeRolePermissionRepo(RolePermissionRepository):
    """内存角色-权限 Repository。"""

    def __init__(self) -> None:
        self._referenced_codes: set[str] = set()

    async def set_for_role(self, role_id: UUID, permission_codes: frozenset[str]) -> None:
        pass

    async def get_for_role(self, role_id: UUID) -> frozenset[str]:  # noqa: ARG002
        return frozenset()

    async def get_for_user(self, user_id: UUID) -> frozenset[str]:  # noqa: ARG002
        return frozenset()

    async def get_all_referenced_codes(self) -> frozenset[str]:
        return frozenset(self._referenced_codes)

    def add_referenced_code(self, code: str) -> None:
        """测试辅助：添加一个被角色引用的权限编码。"""
        self._referenced_codes.add(code)


class FakeRoleRepo(RoleRepository):
    async def add(self, entity: Role) -> None:
        pass

    async def get_by_id(self, role_id: UUID) -> Role | None:
        return None  # noqa: ARG002

    async def get_by_code(self, code: str) -> Role | None:
        return None  # noqa: ARG002

    async def count(self) -> int:
        return 0

    async def list_paginated(self, offset: int, limit: int) -> list[Role]:
        return []  # noqa: ARG002

    async def update(self, entity: Role) -> None:
        pass

    async def list_all(self) -> list[Role]:
        return []


class FakeUserRoleRepo(UserRoleRepository):
    async def assign(self, **kwargs: object) -> None:
        pass

    async def remove(self, user_id: UUID, role_id: UUID) -> None:
        pass  # noqa: ARG002

    async def get_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        return []  # noqa: ARG002

    async def get_active_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        return []  # noqa: ARG002

    async def get_user_ids_for_role(self, role_id: UUID) -> list[UUID]:
        return []  # noqa: ARG002

    async def get_super_admin_user_ids(self) -> list[UUID]:
        return []


class FakeRbacUow(RbacUnitOfWork):
    """内存 RBAC 工作单元。"""

    def __init__(
        self,
        permission_point_repo: FakePermissionPointRepo,
        role_permission_repo: FakeRolePermissionRepo,
    ) -> None:
        self._pp_repo = permission_point_repo
        self._rp_repo = role_permission_repo
        self._role_repo = FakeRoleRepo()
        self._user_role_repo = FakeUserRoleRepo()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    @property
    def roles(self) -> FakeRoleRepo:
        return self._role_repo

    @property
    def user_roles(self) -> FakeUserRoleRepo:
        return self._user_role_repo

    @property
    def role_permissions(self) -> FakeRolePermissionRepo:
        return self._rp_repo

    @property
    def permission_points(self) -> FakePermissionPointRepo:
        return self._pp_repo


def _make_module(
    code: str,
    permissions: frozenset[PermissionPoint],
) -> ModuleDefinition:
    """构造一个最小化的 ModuleDefinition 用于测试。"""
    return ModuleDefinition(
        code=code,
        name=f"Test Module {code}",
        description="Test",
        application_port=object,
        api_tag=code,
        permission_points=permissions,
        routers=(APIRouter(),),
    )


class TestPermissionSync:
    """权限点同步测试。"""

    async def test_first_sync_adds_all_declared(self) -> None:
        """首次同步全部声明的权限点。"""
        pp_repo = FakePermissionPointRepo()
        rp_repo = FakeRolePermissionRepo()

        modules = [
            _make_module(
                "user",
                frozenset(
                    {
                        PermissionPoint(code="system:user:read", description="读取用户"),
                        PermissionPoint(code="system:user:create", description="创建用户"),
                    }
                ),
            ),
            _make_module(
                "rbac",
                frozenset(
                    {
                        PermissionPoint(code="system:role:read", description="读取角色"),
                    }
                ),
            ),
        ]

        def factory() -> FakeRbacUow:
            return FakeRbacUow(pp_repo, rp_repo)

        service = PermissionSyncService(factory, modules)
        result = await service.sync(current_time=_NOW)

        assert len(result.added) == 3
        assert result.total_declared == 3
        assert len(result.orphans) == 0

    async def test_second_sync_is_idempotent(self) -> None:
        """连续同步两次结果一致（幂等）。"""
        pp_repo = FakePermissionPointRepo()
        rp_repo = FakeRolePermissionRepo()

        modules = [
            _make_module(
                "user",
                frozenset(
                    {
                        PermissionPoint(code="system:user:read", description="读取用户"),
                        PermissionPoint(code="system:user:create", description="创建用户"),
                    }
                ),
            ),
        ]

        def factory() -> FakeRbacUow:
            return FakeRbacUow(pp_repo, rp_repo)

        service = PermissionSyncService(factory, modules)

        first = await service.sync(current_time=_NOW)
        assert len(first.added) == 2

        second = await service.sync(current_time=_NOW)
        assert len(second.added) == 0
        assert len(second.unchanged) == 2
        assert second.total_declared == 2

    async def test_update_on_description_change(self) -> None:
        """描述变化时标记为更新。"""
        pp_repo = FakePermissionPointRepo()
        rp_repo = FakeRolePermissionRepo()

        modules = [
            _make_module(
                "user",
                frozenset(
                    {
                        PermissionPoint(code="system:user:read", description="读取用户"),
                    }
                ),
            ),
        ]

        def factory() -> FakeRbacUow:
            return FakeRbacUow(pp_repo, rp_repo)

        service = PermissionSyncService(factory, modules)
        await service.sync(current_time=_NOW)

        modules_updated = [
            _make_module(
                "user",
                frozenset(
                    {
                        PermissionPoint(code="system:user:read", description="查看用户详情"),
                    }
                ),
            ),
        ]
        service2 = PermissionSyncService(factory, modules_updated)
        result = await service2.sync(current_time=_NOW)

        assert len(result.updated) == 1
        assert "system:user:read" in result.updated

    async def test_orphan_detection(self) -> None:
        """孤立权限点检测——DB 中有但代码中未声明。"""
        pp_repo = FakePermissionPointRepo()
        rp_repo = FakeRolePermissionRepo()

        # 预置一个孤立的权限点
        await pp_repo.upsert(
            code="system:old:removed",
            description="已移除的权限",
            module_code="old_module",
            current_time=_NOW,
        )

        modules = [
            _make_module(
                "user",
                frozenset(
                    {
                        PermissionPoint(code="system:user:read", description="读取用户"),
                    }
                ),
            ),
        ]

        def factory() -> FakeRbacUow:
            return FakeRbacUow(pp_repo, rp_repo)

        service = PermissionSyncService(factory, modules)
        result = await service.sync(current_time=_NOW)

        assert "system:old:removed" in result.orphans

    async def test_orphan_not_deleted(self) -> None:
        """孤立权限点不自动删除。"""
        pp_repo = FakePermissionPointRepo()
        rp_repo = FakeRolePermissionRepo()

        await pp_repo.upsert(
            code="system:old:removed",
            description="已移除的权限",
            module_code="old_module",
            current_time=_NOW,
        )

        modules: list[ModuleDefinition] = []

        def factory() -> FakeRbacUow:
            return FakeRbacUow(pp_repo, rp_repo)

        service = PermissionSyncService(factory, modules)
        await service.sync(current_time=_NOW)

        # 孤立权限点仍在 DB 中
        all_codes = [code for code, _, _ in await pp_repo.list_all()]
        assert "system:old:removed" in all_codes

    async def test_orphan_referenced_by_role(self) -> None:
        """孤立且仍被角色引用的权限点被标记。"""
        pp_repo = FakePermissionPointRepo()
        rp_repo = FakeRolePermissionRepo()

        # 预置一个被角色引用的孤立权限点
        await pp_repo.upsert(
            code="system:old:removed",
            description="已移除的权限",
            module_code="old_module",
            current_time=_NOW,
        )
        rp_repo.add_referenced_code("system:old:removed")

        modules: list[ModuleDefinition] = []

        def factory() -> FakeRbacUow:
            return FakeRbacUow(pp_repo, rp_repo)

        service = PermissionSyncService(factory, modules)
        result = await service.sync(current_time=_NOW)

        assert "system:old:removed" in result.orphans
        assert "system:old:removed" in result.orphan_referenced

    async def test_consecutive_syncs_produce_identical_db(self) -> None:
        """连续执行两次后 DB 中权限点一致（SPEC §34.2）。"""
        pp_repo = FakePermissionPointRepo()
        rp_repo = FakeRolePermissionRepo()

        modules = [
            _make_module(
                "user",
                frozenset(
                    {
                        PermissionPoint(code="system:user:read", description="读取用户"),
                        PermissionPoint(code="system:user:create", description="创建用户"),
                    }
                ),
            ),
            _make_module(
                "rbac",
                frozenset(
                    {
                        PermissionPoint(code="system:role:read", description="读取角色"),
                        PermissionPoint(code="system:role:create", description="创建角色"),
                    }
                ),
            ),
        ]

        def factory() -> FakeRbacUow:
            return FakeRbacUow(pp_repo, rp_repo)

        service = PermissionSyncService(factory, modules)

        await service.sync(current_time=_NOW)
        db_after_first = sorted(await pp_repo.list_all())

        await service.sync(current_time=_NOW)
        db_after_second = sorted(await pp_repo.list_all())

        assert db_after_first == db_after_second
