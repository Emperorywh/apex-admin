"""RBAC 模块领域层与应用服务单元测试（SPEC §13.1–13.4、§23.5）。

使用内存 Fake UoW 和 Repository 验证 Use Case 的编排逻辑、
领域策略、管理范围强制和超级管理员保护。
不依赖数据库或 Docker。

覆盖范围：
- 角色领域实体创建、不变性和状态转换
- 角色 CRUD Use Case（创建、详情、列表、更新、启用、禁用）
- 角色-权限分配（含管理范围二次校验）
- 用户-角色分配（含管理范围和目标用户可管理性校验）
- 超级管理员保护（最后管理员、内置角色保护）
- 权限即时生效（基于 DB 非缓存）
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from app.errors import AuthorizationError, ConflictError, NotFoundError
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.rbac.application.port import (
    PermissionPointRecord,
    RbacUnitOfWork,
    RolePermissionRepository,
    RoleRepository,
    UserRoleRepository,
)
from app.modules.rbac.application.service import RbacService
from app.modules.rbac.domain.model import Role, RoleStatus
from app.modules.registry import ModuleRegistry

pytestmark = [pytest.mark.unit, pytest.mark.g2]


# ===========================================================================
# Fake 实现（内存，不依赖数据库）
# ===========================================================================


class FakeRoleRepository(RoleRepository):
    """内存角色 Repository。"""

    def __init__(self) -> None:
        self._roles: dict[UUID, Role] = {}

    async def add(self, entity: Role) -> None:
        self._roles[entity.id] = entity

    async def get_by_id(self, role_id: UUID) -> Role | None:
        return self._roles.get(role_id)

    async def get_by_code(self, code: str) -> Role | None:
        for r in self._roles.values():
            if r.code == code:
                return r
        return None

    async def count(self) -> int:
        return len(self._roles)

    async def list_paginated(self, offset: int, limit: int) -> list[Role]:
        all_roles = sorted(self._roles.values(), key=lambda r: r.created_at, reverse=True)
        return all_roles[offset : offset + limit]

    async def update(self, entity: Role) -> None:
        self._roles[entity.id] = entity

    async def list_all(self) -> list[Role]:
        return list(self._roles.values())


class FakeUserRoleRepository(UserRoleRepository):
    """内存用户-角色关系 Repository。"""

    def __init__(self) -> None:
        # (user_id, role_id) -> (assigned_at, assigned_by)
        self._assignments: dict[tuple[UUID, UUID], tuple[datetime, UUID | None]] = {}

    async def assign(
        self,
        *,
        user_id: UUID,
        role_id: UUID,
        assigned_at: datetime,
        assigned_by: UUID | None = None,
    ) -> None:
        key = (user_id, role_id)
        if key not in self._assignments:
            self._assignments[key] = (assigned_at, assigned_by)

    async def remove(self, user_id: UUID, role_id: UUID) -> None:
        self._assignments.pop((user_id, role_id), None)

    async def get_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        return [rid for (uid, rid) in self._assignments if uid == user_id]

    async def get_active_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        return [rid for (uid, rid) in self._assignments if uid == user_id]

    async def get_user_ids_for_role(self, role_id: UUID) -> list[UUID]:
        return [uid for (uid, rid) in self._assignments if rid == role_id]

    async def get_super_admin_user_ids(self) -> list[UUID]:
        raise NotImplementedError("由 FakeUserRoleRepositoryWithQueries 实现")


class FakeRolePermissionRepository(RolePermissionRepository):
    """内存角色-权限关系 Repository。"""

    def __init__(self) -> None:
        # role_id -> set of permission codes
        self._permissions: dict[UUID, set[str]] = {}

    async def set_for_role(self, role_id: UUID, permission_codes: frozenset[str]) -> None:
        self._permissions[role_id] = set(permission_codes)

    async def get_for_role(self, role_id: UUID) -> frozenset[str]:
        return frozenset(self._permissions.get(role_id, set()))

    async def get_for_user(self, user_id: UUID) -> frozenset[str]:
        raise NotImplementedError("由 FakeRolePermissionRepositoryWithQueries 实现")

    async def get_all_referenced_codes(self) -> frozenset[str]:
        result: set[str] = set()
        for codes in self._permissions.values():
            result |= codes
        return frozenset(result)


class FakeUserRoleRepositoryWithQueries(FakeUserRoleRepository):
    """支持超级管理员查询和启用角色过滤的 Fake UserRoleRepository。"""

    def __init__(self, roles_repo: FakeRoleRepository) -> None:
        super().__init__()
        self._roles_repo = roles_repo

    async def get_active_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        all_ids = await super().get_active_role_ids_for_user(user_id)
        result: list[UUID] = []
        for rid in all_ids:
            role = await self._roles_repo.get_by_id(rid)
            if role is not None and role.is_active:
                result.append(rid)
        return result

    async def get_super_admin_user_ids(self) -> list[UUID]:
        result: set[UUID] = set()
        for uid, rid in list(self._assignments.keys()):
            role = await self._roles_repo.get_by_id(rid)
            if role is not None and role.is_super_admin and role.is_active:
                result.add(uid)
        return list(result)


class FakePermissionPointRepo(PermissionPointRecord):
    """内存权限点注册表 Repository。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}

    async def upsert(
        self,
        code: str,
        description: str,
        module_code: str,
        current_time: datetime,  # noqa: ARG002
    ) -> bool:
        existing = self._data.get(code)
        if existing is None:
            self._data[code] = {"description": description, "module_code": module_code}
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


class FakeRolePermissionRepositoryWithQueries(FakeRolePermissionRepository):
    """支持用户权限查询的 Fake RolePermissionRepository。"""

    def __init__(
        self,
        user_roles_repo: FakeUserRoleRepositoryWithQueries,
        roles_repo: FakeRoleRepository,
    ) -> None:
        super().__init__()
        self._user_roles_repo = user_roles_repo
        self._roles_repo = roles_repo

    async def get_for_user(self, user_id: UUID) -> frozenset[str]:
        role_ids = await self._user_roles_repo.get_active_role_ids_for_user(user_id)
        perms: set[str] = set()
        for rid in role_ids:
            role = await self._roles_repo.get_by_id(rid)
            if role is not None and role.is_active:
                perms |= self._permissions.get(rid, set())
        return frozenset(perms)


class FakeRbacUnitOfWork(RbacUnitOfWork):
    """内存 RBAC UoW。"""

    def __init__(
        self,
        roles: FakeRoleRepository,
        user_roles: FakeUserRoleRepositoryWithQueries,
        role_permissions: FakeRolePermissionRepositoryWithQueries,
    ) -> None:
        self._roles_repo = roles
        self._user_roles_repo = user_roles
        self._role_permissions_repo = role_permissions
        self._permission_points_repo = FakePermissionPointRepo()

    @property
    def roles(self) -> FakeRoleRepository:
        return self._roles_repo

    @property
    def user_roles(self) -> FakeUserRoleRepositoryWithQueries:
        return self._user_roles_repo

    @property
    def role_permissions(self) -> FakeRolePermissionRepositoryWithQueries:
        return self._role_permissions_repo

    @property
    def permission_points(self) -> FakePermissionPointRepo:
        return self._permission_points_repo

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


def create_fake_uow() -> FakeRbacUnitOfWork:
    """创建内存 Fake UoW 并返回。"""
    roles = FakeRoleRepository()
    user_roles = FakeUserRoleRepositoryWithQueries(roles)
    role_permissions = FakeRolePermissionRepositoryWithQueries(user_roles, roles)
    return FakeRbacUnitOfWork(roles, user_roles, role_permissions)


def _make_dispatcher() -> TransactionalEventDispatcher:
    """构造带空处理器注册表的事件调度器。"""
    empty_registry = EventHandlerRegistry(ModuleRegistry([]), {})
    return TransactionalEventDispatcher(empty_registry)


def create_service(uow: FakeRbacUnitOfWork) -> RbacService:
    """从 Fake UoW 创建 RbacService。"""
    return RbacService(uow_factory=lambda: uow, event_dispatcher=_make_dispatcher())


_NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


# ===========================================================================
# 领域实体测试
# ===========================================================================


class TestRoleEntity:
    """角色领域实体测试（SPEC §13.1）。"""

    def test_new_role_defaults(self):
        role = Role.new(code="editor", name="编辑", current_time=_NOW)
        assert role.status is RoleStatus.ACTIVE
        assert role.is_builtin is False
        assert role.is_super_admin is False
        assert role.description is None
        assert role.is_active is True
        assert role.is_disabled is False

    def test_enable(self):
        role = Role.new(code="r", name="R", current_time=_NOW)
        disabled = role.disable(current_time=_NOW)
        assert disabled.is_disabled
        reenabled = disabled.enable(current_time=_NOW)
        assert reenabled.is_active

    def test_disable(self):
        role = Role.new(code="r", name="R", current_time=_NOW)
        disabled = role.disable(current_time=_NOW)
        assert disabled.is_disabled
        assert disabled.status is RoleStatus.DISABLED

    def test_update_name_and_description(self):
        role = Role.new(code="r", name="Old", current_time=_NOW)
        updated = role.update(
            field_updates={"name": "New", "description": "desc"},
            current_time=_NOW,
        )
        assert updated.name == "New"
        assert updated.description == "desc"

    def test_role_is_frozen(self):
        from dataclasses import FrozenInstanceError

        role = Role.new(code="r", name="R", current_time=_NOW)
        with pytest.raises(FrozenInstanceError):
            role.name = "X"  # type: ignore[misc]

    def test_super_admin_flag(self):
        role = Role.new(
            code="super_admin",
            name="超级管理员",
            is_super_admin=True,
            is_builtin=True,
            current_time=_NOW,
        )
        assert role.is_super_admin
        assert role.is_builtin


# ===========================================================================
# 角色 CRUD Use Case 测试
# ===========================================================================


class TestRoleCRUD:
    """角色 CRUD Use Case 测试（SPEC §13.2）。"""

    async def test_create_role_success(self):
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="editor",
            name="编辑",
            description="内容编辑",
            is_super_admin=False,
            current_time=_NOW,
        )
        assert role.code == "editor"
        assert role.name == "编辑"
        assert role.is_active

    async def test_create_role_duplicate_code(self):
        uow = create_fake_uow()
        service = create_service(uow)
        await service.create_role(
            code="editor",
            name="编辑",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        with pytest.raises(ConflictError, match="RBAC.ROLE_ALREADY_EXISTS"):
            await service.create_role(
                code="editor",
                name="编辑2",
                description=None,
                is_super_admin=False,
                current_time=_NOW,
            )

    async def test_get_role_not_found(self):
        uow = create_fake_uow()
        service = create_service(uow)
        with pytest.raises(NotFoundError, match="RBAC.ROLE_NOT_FOUND"):
            await service.get_role(uuid4())

    async def test_list_roles(self):
        uow = create_fake_uow()
        service = create_service(uow)
        for i in range(5):
            await service.create_role(
                code=f"role_{i}",
                name=f"角色{i}",
                description=None,
                is_super_admin=False,
                current_time=_NOW,
            )
        roles, total = await service.list_roles(page=1, page_size=3)
        assert total == 5
        assert len(roles) == 3

    async def test_update_role(self):
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="r",
            name="Old",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        updated = await service.update_role(
            role_id=role.id,
            field_updates={"name": "New"},
            current_time=_NOW,
        )
        assert updated.name == "New"

    async def test_enable_role(self):
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="r",
            name="R",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await service.disable_role(role_id=role.id, current_time=_NOW)
        enabled = await service.enable_role(role_id=role.id, current_time=_NOW)
        assert enabled.is_active

    async def test_disable_builtin_super_admin_protected(self):
        uow = create_fake_uow()
        service = create_service(uow)
        role = Role.new(
            code="super_admin",
            name="超级管理员",
            is_super_admin=True,
            is_builtin=True,
            current_time=_NOW,
        )
        await uow.roles.add(role)
        with pytest.raises(ConflictError, match="RBAC.BUILTIN_ROLE_PROTECTED"):
            await service.disable_role(role_id=role.id, current_time=_NOW)

    async def test_disable_normal_role_allowed(self):
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="r",
            name="R",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        disabled = await service.disable_role(role_id=role.id, current_time=_NOW)
        assert disabled.is_disabled


# ===========================================================================
# 角色-权限分配测试
# ===========================================================================


class TestPermissionAssignment:
    """角色-权限分配测试（SPEC §13.2、§13.3）。"""

    async def test_assign_permissions_no_actor(self):
        """无 actor_id 时跳过范围检查。"""
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="r",
            name="R",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        codes = await service.assign_permissions_to_role(
            role_id=role.id,
            permission_codes=frozenset({"system:user:read", "system:role:read"}),
            current_time=_NOW,
        )
        assert codes == frozenset({"system:user:read", "system:role:read"})
        stored = await service.get_role_permissions(role.id)
        assert stored == codes

    async def test_assign_permissions_within_scope(self):
        """普通管理员只能分配自身范围内的权限。"""
        uow = create_fake_uow()
        service = create_service(uow)
        actor_role = await service.create_role(
            code="admin",
            name="管理员",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(
            actor_role.id,
            frozenset({"system:user:read", "system:role:read"}),
        )
        actor_id = uuid4()
        await uow.user_roles.assign(
            user_id=actor_id,
            role_id=actor_role.id,
            assigned_at=_NOW,
        )
        target_role = await service.create_role(
            code="editor",
            name="编辑",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        codes = await service.assign_permissions_to_role(
            role_id=target_role.id,
            permission_codes=frozenset({"system:user:read"}),
            current_time=_NOW,
            actor_id=actor_id,
        )
        assert "system:user:read" in codes

    async def test_assign_permissions_out_of_scope(self):
        """普通管理员分配超出范围的权限——拒绝。"""
        uow = create_fake_uow()
        service = create_service(uow)
        actor_role = await service.create_role(
            code="admin",
            name="管理员",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(
            actor_role.id,
            frozenset({"system:user:read"}),
        )
        actor_id = uuid4()
        await uow.user_roles.assign(
            user_id=actor_id,
            role_id=actor_role.id,
            assigned_at=_NOW,
        )
        target_role = await service.create_role(
            code="editor",
            name="编辑",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        with pytest.raises(AuthorizationError, match="RBAC.INSUFFICIENT_SCOPE"):
            await service.assign_permissions_to_role(
                role_id=target_role.id,
                permission_codes=frozenset({"system:user:delete"}),
                current_time=_NOW,
                actor_id=actor_id,
            )

    async def test_assign_permissions_super_admin_bypass(self):
        """超级管理员不受范围限制。"""
        uow = create_fake_uow()
        service = create_service(uow)
        super_role = Role.new(
            code="super_admin",
            name="超级管理员",
            is_super_admin=True,
            current_time=_NOW,
        )
        await uow.roles.add(super_role)
        actor_id = uuid4()
        await uow.user_roles.assign(
            user_id=actor_id,
            role_id=super_role.id,
            assigned_at=_NOW,
        )
        target_role = await service.create_role(
            code="editor",
            name="编辑",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        codes = await service.assign_permissions_to_role(
            role_id=target_role.id,
            permission_codes=frozenset({"any:permission:code"}),
            current_time=_NOW,
            actor_id=actor_id,
        )
        assert "any:permission:code" in codes


# ===========================================================================
# 用户-角色分配测试
# ===========================================================================


class TestUserRoleAssignment:
    """用户-角色分配测试（SPEC §13.2、§13.3、§13.4）。"""

    async def test_assign_roles_no_actor(self):
        """无 actor_id 时跳过范围检查。"""
        uow = create_fake_uow()
        service = create_service(uow)
        await service.create_role(
            code="editor",
            name="编辑",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        user_id = uuid4()
        await service.assign_roles_to_user(
            user_id=user_id,
            role_codes=frozenset({"editor"}),
            current_time=_NOW,
        )
        roles = await service.get_user_roles(user_id)
        assert len(roles) == 1
        assert roles[0].code == "editor"

    async def test_assign_roles_role_not_found(self):
        uow = create_fake_uow()
        service = create_service(uow)
        with pytest.raises(NotFoundError, match="RBAC.ROLE_NOT_FOUND"):
            await service.assign_roles_to_user(
                user_id=uuid4(),
                role_codes=frozenset({"nonexistent"}),
                current_time=_NOW,
            )

    async def test_assign_roles_within_scope(self):
        """普通管理员只能授予自身范围内的角色。"""
        uow = create_fake_uow()
        service = create_service(uow)
        actor_role = await service.create_role(
            code="admin",
            name="管理员",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(
            actor_role.id,
            frozenset({"system:user:read"}),
        )
        actor_id = uuid4()
        await uow.user_roles.assign(
            user_id=actor_id,
            role_id=actor_role.id,
            assigned_at=_NOW,
        )
        target_role = await service.create_role(
            code="viewer",
            name="查看者",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(
            target_role.id,
            frozenset({"system:user:read"}),
        )
        target_user = uuid4()
        await service.assign_roles_to_user(
            user_id=target_user,
            role_codes=frozenset({"viewer"}),
            current_time=_NOW,
            actor_id=actor_id,
        )
        roles = await service.get_user_roles(target_user)
        assert len(roles) == 1

    async def test_assign_roles_out_of_scope(self):
        """普通管理员授予超出范围的角色——拒绝。"""
        uow = create_fake_uow()
        service = create_service(uow)
        actor_role = await service.create_role(
            code="admin",
            name="管理员",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(
            actor_role.id,
            frozenset({"system:user:read"}),
        )
        actor_id = uuid4()
        await uow.user_roles.assign(
            user_id=actor_id,
            role_id=actor_role.id,
            assigned_at=_NOW,
        )
        target_role = await service.create_role(
            code="power",
            name="高级",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(
            target_role.id,
            frozenset({"system:user:read", "system:user:delete"}),
        )
        with pytest.raises(AuthorizationError, match="RBAC.INSUFFICIENT_SCOPE"):
            await service.assign_roles_to_user(
                user_id=uuid4(),
                role_codes=frozenset({"power"}),
                current_time=_NOW,
                actor_id=actor_id,
            )

    async def test_assign_roles_super_admin_bypass(self):
        """超级管理员不受范围限制。"""
        uow = create_fake_uow()
        service = create_service(uow)
        super_role = Role.new(
            code="super_admin",
            name="超级管理员",
            is_super_admin=True,
            current_time=_NOW,
        )
        await uow.roles.add(super_role)
        actor_id = uuid4()
        await uow.user_roles.assign(
            user_id=actor_id,
            role_id=super_role.id,
            assigned_at=_NOW,
        )
        target_role = await service.create_role(
            code="any_role",
            name="任意角色",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(
            target_role.id,
            frozenset({"any:thing:here"}),
        )
        await service.assign_roles_to_user(
            user_id=uuid4(),
            role_codes=frozenset({"any_role"}),
            current_time=_NOW,
            actor_id=actor_id,
        )

    async def test_remove_roles_last_super_admin_protected(self):
        """移除最后一个超级管理员——拒绝（SPEC §13.4）。"""
        uow = create_fake_uow()
        service = create_service(uow)
        super_role = Role.new(
            code="super_admin",
            name="超级管理员",
            is_super_admin=True,
            current_time=_NOW,
        )
        await uow.roles.add(super_role)
        only_super = uuid4()
        await uow.user_roles.assign(
            user_id=only_super,
            role_id=super_role.id,
            assigned_at=_NOW,
        )
        with pytest.raises(ConflictError, match="RBAC.LAST_SUPER_ADMIN"):
            await service.remove_roles_from_user(
                user_id=only_super,
                role_codes=frozenset({"super_admin"}),
                current_time=_NOW,
            )

    async def test_remove_roles_multiple_super_admins_allowed(self):
        """有多个超级管理员时可以移除其中一个。"""
        uow = create_fake_uow()
        service = create_service(uow)
        super_role = Role.new(
            code="super_admin",
            name="超级管理员",
            is_super_admin=True,
            current_time=_NOW,
        )
        await uow.roles.add(super_role)
        user1 = uuid4()
        user2 = uuid4()
        await uow.user_roles.assign(user_id=user1, role_id=super_role.id, assigned_at=_NOW)
        await uow.user_roles.assign(user_id=user2, role_id=super_role.id, assigned_at=_NOW)
        await service.remove_roles_from_user(
            user_id=user1,
            role_codes=frozenset({"super_admin"}),
            current_time=_NOW,
        )
        remaining = await service.get_user_roles(user1)
        assert len(remaining) == 0

    async def test_get_role_members(self):
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="r",
            name="R",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        u1, u2 = uuid4(), uuid4()
        await uow.user_roles.assign(user_id=u1, role_id=role.id, assigned_at=_NOW)
        await uow.user_roles.assign(user_id=u2, role_id=role.id, assigned_at=_NOW)
        members = await service.get_role_members(role.id)
        assert set(members) == {u1, u2}


# ===========================================================================
# 权限查询测试
# ===========================================================================


class TestPermissionQuery:
    """权限查询测试（SPEC §13.2 管理范围、§13.3 权限即时生效）。"""

    async def test_get_user_permissions_union(self):
        """用户权限是全部启用角色权限的并集。"""
        uow = create_fake_uow()
        service = create_service(uow)
        role1 = await service.create_role(
            code="r1",
            name="R1",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        role2 = await service.create_role(
            code="r2",
            name="R2",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(role1.id, frozenset({"a:b:c", "d:e:f"}))
        await uow.role_permissions.set_for_role(role2.id, frozenset({"x:y:z"}))
        user_id = uuid4()
        await uow.user_roles.assign(user_id=user_id, role_id=role1.id, assigned_at=_NOW)
        await uow.user_roles.assign(user_id=user_id, role_id=role2.id, assigned_at=_NOW)
        perms = await service.get_user_permissions(user_id)
        assert perms == frozenset({"a:b:c", "d:e:f", "x:y:z"})

    async def test_disabled_role_excluded(self):
        """禁用角色的权限不计入用户范围。"""
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="r",
            name="R",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        await uow.role_permissions.set_for_role(role.id, frozenset({"a:b:c"}))
        user_id = uuid4()
        await uow.user_roles.assign(user_id=user_id, role_id=role.id, assigned_at=_NOW)
        await service.disable_role(role_id=role.id, current_time=_NOW)
        perms = await service.get_user_permissions(user_id)
        assert perms == frozenset()

    async def test_is_user_super_admin(self):
        """超级管理员检测基于角色标志。"""
        uow = create_fake_uow()
        service = create_service(uow)
        super_role = Role.new(
            code="super_admin",
            name="SA",
            is_super_admin=True,
            current_time=_NOW,
        )
        await uow.roles.add(super_role)
        super_user = uuid4()
        normal_user = uuid4()
        await uow.user_roles.assign(
            user_id=super_user,
            role_id=super_role.id,
            assigned_at=_NOW,
        )
        assert await service.is_user_super_admin(super_user) is True
        assert await service.is_user_super_admin(normal_user) is False

    async def test_permissions_take_effect_immediately(self):
        """权限变更后立即生效——基于 DB 查询非缓存（SPEC §13.3）。"""
        uow = create_fake_uow()
        service = create_service(uow)
        role = await service.create_role(
            code="r",
            name="R",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )
        user_id = uuid4()
        await uow.user_roles.assign(user_id=user_id, role_id=role.id, assigned_at=_NOW)

        # 初始无权限
        perms = await service.get_user_permissions(user_id)
        assert perms == frozenset()

        # 分配权限后立即生效
        await uow.role_permissions.set_for_role(role.id, frozenset({"new:perm:code"}))
        perms = await service.get_user_permissions(user_id)
        assert "new:perm:code" in perms


# ===========================================================================
# 模块定义测试
# ===========================================================================


class TestRbacModuleDefinition:
    """RBAC 模块定义测试（SPEC §5.5）。"""

    def test_module_code_is_rbac(self) -> None:
        from app.modules.rbac.definition import MODULE

        assert MODULE.code == "rbac"

    def test_permission_points_format(self) -> None:
        from app.modules.rbac.definition import MODULE

        for perm in MODULE.permission_points:
            parts = perm.code.split(":")
            assert len(parts) >= 3
            assert parts[0] == "system"

    def test_error_codes_format(self) -> None:
        from app.modules.rbac.definition import MODULE

        for err in MODULE.error_codes:
            assert err.code.startswith("RBAC.")

    def test_event_handlers_match_events(self) -> None:
        from app.modules.rbac.definition import MODULE

        event_codes = {e.code for e in MODULE.events}
        for handler in MODULE.event_handlers:
            assert handler.event_code in event_codes

    def test_registry_validates_without_conflicts(self) -> None:
        from app.modules.auth.definition import MODULE as AUTH_MODULE
        from app.modules.rbac.definition import MODULE
        from app.modules.user.definition import MODULE as USER_MODULE

        registry = ModuleRegistry([USER_MODULE, AUTH_MODULE, MODULE])
        assert registry.get_module("rbac") is MODULE


# ===========================================================================
# 响应 Schema 安全测试
# ===========================================================================


class TestRbacResponseSecurity:
    """RBAC Schema 安全测试（SPEC §9.2）。"""

    def test_all_request_schemas_have_extra_forbid(self) -> None:
        from app.modules.rbac.application.schemas import (
            AssignPermissionsRequest,
            AssignRolesRequest,
            CreateRoleRequest,
            RemoveRolesRequest,
            UpdateRoleRequest,
        )

        for schema_cls in [
            CreateRoleRequest,
            UpdateRoleRequest,
            AssignPermissionsRequest,
            AssignRolesRequest,
            RemoveRolesRequest,
        ]:
            assert schema_cls.model_config.get("extra") == "forbid", (
                f"{schema_cls.__name__} 应设置 extra='forbid'"
            )
