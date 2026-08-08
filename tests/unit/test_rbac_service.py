"""RBAC 模块应用服务单元测试（SPEC §13.1–13.4）。

使用内存假实现替代数据库，测试 RbacService 的核心 Use Case：

- 角色 CRUD：创建、查询、更新、禁用
- 权限分配：scope 强制、超级管理员绕过
- 用户-角色管理：分配、移除、最后管理员保护
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from app.errors import AuthorizationError, ConflictError, NotFoundError
from app.modules.rbac.application.port import (
    PermissionPointRecord,
    RbacUnitOfWork,
    RolePermissionRepository,
    RoleRepository,
    UserRoleRepository,
)
from app.modules.rbac.domain.model import Role, RoleStatus
from app.ports.audit import AuditDiff, AuditPort, AuditResult

pytestmark = [pytest.mark.unit, pytest.mark.g2]

_NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


# ===========================================================================
# 内存假实现
# ===========================================================================


class FakeRoleRepo(RoleRepository):
    """内存角色 Repository。"""

    def __init__(self) -> None:
        self._by_id: dict[UUID, Role] = {}
        self._by_code: dict[str, Role] = {}

    async def add(self, entity: Role) -> None:
        self._by_id[entity.id] = entity
        self._by_code[entity.code] = entity

    async def get_by_id(self, role_id: UUID) -> Role | None:
        return self._by_id.get(role_id)

    async def get_by_code(self, code: str) -> Role | None:
        return self._by_code.get(code)

    async def count(self) -> int:
        return len(self._by_id)

    async def list_paginated(self, offset: int, limit: int) -> list[Role]:
        all_roles = list(self._by_id.values())
        return all_roles[offset : offset + limit]

    async def update(self, entity: Role) -> None:
        self._by_id[entity.id] = entity
        self._by_code[entity.code] = entity

    async def list_all(self) -> list[Role]:
        return list(self._by_id.values())


class FakeUserRoleRepo(UserRoleRepository):
    """内存用户-角色 Repository。"""

    def __init__(self) -> None:
        self._by_user: dict[UUID, list[UUID]] = {}
        self._by_role: dict[UUID, list[UUID]] = {}
        self._super_admin_users: set[UUID] = set()

    async def assign(self, **kwargs: object) -> None:
        user_id = kwargs["user_id"]  # type: ignore[assignment]
        role_id = kwargs["role_id"]  # type: ignore[assignment]
        self._by_user.setdefault(user_id, []).append(role_id)  # type: ignore[arg-type]
        self._by_role.setdefault(role_id, []).append(user_id)  # type: ignore[arg-type]

    async def remove(self, user_id: UUID, role_id: UUID) -> None:
        if user_id in self._by_user:
            self._by_user[user_id] = [r for r in self._by_user[user_id] if r != role_id]
        if role_id in self._by_role:
            self._by_role[role_id] = [u for u in self._by_role[role_id] if u != user_id]

    async def get_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        return list(self._by_user.get(user_id, []))

    async def get_active_role_ids_for_user(self, user_id: UUID) -> list[UUID]:
        return list(self._by_user.get(user_id, []))

    async def get_user_ids_for_role(self, role_id: UUID) -> list[UUID]:
        return list(self._by_role.get(role_id, []))

    async def get_super_admin_user_ids(self) -> list[UUID]:
        return list(self._super_admin_users)


class FakeRolePermissionRepo(RolePermissionRepository):
    """内存角色-权限 Repository。"""

    def __init__(self) -> None:
        self._by_role: dict[UUID, set[str]] = {}
        self._referenced: set[str] = set()

    async def set_for_role(self, role_id: UUID, permission_codes: frozenset[str]) -> None:
        self._by_role[role_id] = set(permission_codes)
        self._referenced.update(permission_codes)

    async def get_for_role(self, role_id: UUID) -> frozenset[str]:
        return frozenset(self._by_role.get(role_id, set()))

    async def get_for_user(self, user_id: UUID) -> frozenset[str]:  # noqa: ARG002
        return frozenset()

    async def get_all_referenced_codes(self) -> frozenset[str]:
        return frozenset(self._referenced)


class FakePermissionPointRepo(PermissionPointRecord):
    """内存权限点 Repository。"""

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
        self._data[code] = {"description": description, "module_code": module_code}
        return existing is None or existing != self._data[code]

    async def list_all(self) -> list[tuple[str, str, str]]:
        return [(c, r["description"], r["module_code"]) for c, r in sorted(self._data.items())]

    async def delete(self, code: str) -> None:
        self._data.pop(code, None)


class FakeRbacUow(RbacUnitOfWork):
    """内存 RBAC 工作单元。"""

    def __init__(self) -> None:
        self._roles = FakeRoleRepo()
        self._user_roles = FakeUserRoleRepo()
        self._role_permissions = FakeRolePermissionRepo()
        self._permission_points = FakePermissionPointRepo()

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
        return self._roles

    @property
    def user_roles(self) -> FakeUserRoleRepo:
        return self._user_roles

    @property
    def role_permissions(self) -> FakeRolePermissionRepo:
        return self._role_permissions

    @property
    def permission_points(self) -> FakePermissionPointRepo:
        return self._permission_points


class _NoOpEventDispatcher:
    def collect(self, event: object) -> None:  # noqa: ARG002
        pass

    async def flush(self, uow: object) -> None:  # noqa: ARG002
        pass


class _NoOpAuditPort(AuditPort):
    async def record(
        self,
        uow: object,  # noqa: ARG002
        *,
        actor_id: UUID | None,  # noqa: ARG002
        actor_display_name: str | None,  # noqa: ARG002
        occurred_at: datetime,  # noqa: ARG002
        module: str,  # noqa: ARG002
        action: str,  # noqa: ARG002
        result: AuditResult,  # noqa: ARG002
        resource_type: str | None = None,  # noqa: ARG002
        resource_id: str | None = None,  # noqa: ARG002
        resource_display_name: str | None = None,  # noqa: ARG002
        request_id: str | None = None,  # noqa: ARG002
        diff: AuditDiff | None = None,  # noqa: ARG002
    ) -> None:
        pass


def _make_service(uow: FakeRbacUow) -> object:
    """构造 RbacService 实例。"""
    from app.modules.rbac.application.service import RbacService

    def factory() -> FakeRbacUow:
        return uow

    return RbacService(
        uow_factory=factory,
        event_dispatcher=_NoOpEventDispatcher(),  # type: ignore[arg-type]
        audit_port=_NoOpAuditPort(),
    )


def _make_role(
    *,
    code: str = "admin",
    name: str = "管理员",
    is_super_admin: bool = False,
    is_builtin: bool = False,
    status: RoleStatus = RoleStatus.ACTIVE,
    current_time: datetime = _NOW,
) -> Role:
    from dataclasses import replace as dc_replace

    role = Role.new(
        code=code,
        name=name,
        is_super_admin=is_super_admin,
        is_builtin=is_builtin,
        current_time=current_time,
    )
    if status is not RoleStatus.ACTIVE:
        role = dc_replace(role, status=status)
    return role


# ===========================================================================
# 角色 CRUD 测试
# ===========================================================================


class TestRoleCrud:
    """角色管理 Use Case 测试（SPEC §13.2）。"""

    async def test_create_role(self) -> None:
        """创建角色成功。"""
        uow = FakeRbacUow()
        service = _make_service(uow)

        role = await service.create_role(  # type: ignore[attr-defined]
            code="editor",
            name="编辑",
            description=None,
            is_super_admin=False,
            current_time=_NOW,
        )

        assert role.code == "editor"
        stored = await uow.roles.get_by_code("editor")
        assert stored is not None

    async def test_get_role_not_found(self) -> None:
        """查询不存在的角色返回 NotFoundError。"""
        uow = FakeRbacUow()
        service = _make_service(uow)

        with pytest.raises(NotFoundError, match="不存在"):
            await service.get_role(uuid4())  # type: ignore[attr-defined]

    async def test_list_roles(self) -> None:
        """分页查询角色列表。"""
        uow = FakeRbacUow()
        await uow.roles.add(_make_role(code="r1", name="R1"))
        await uow.roles.add(_make_role(code="r2", name="R2"))
        service = _make_service(uow)

        roles, total = await service.list_roles(page=1, page_size=10)  # type: ignore[attr-defined]

        assert total == 2
        assert len(roles) == 2

    async def test_update_role(self) -> None:
        """更新角色信息。"""
        uow = FakeRbacUow()
        role = _make_role(code="editor", name="编辑")
        await uow.roles.add(role)
        service = _make_service(uow)

        updated = await service.update_role(  # type: ignore[attr-defined]
            role_id=role.id,
            field_updates={"name": "高级编辑"},
            current_time=_NOW,
        )

        assert updated.name == "高级编辑"

    async def test_update_role_not_found(self) -> None:
        """更新不存在的角色返回 NotFoundError。"""
        uow = FakeRbacUow()
        service = _make_service(uow)

        with pytest.raises(NotFoundError, match="不存在"):
            await service.update_role(  # type: ignore[attr-defined]
                role_id=uuid4(),
                field_updates={"name": "X"},
                current_time=_NOW,
            )

    async def test_disable_role(self) -> None:
        """禁用角色。"""
        uow = FakeRbacUow()
        role = _make_role(code="temp", name="临时")
        await uow.roles.add(role)
        service = _make_service(uow)

        await service.disable_role(role_id=role.id, current_time=_NOW)  # type: ignore[attr-defined]

        stored = await uow.roles.get_by_id(role.id)
        assert stored is not None
        assert stored.status is RoleStatus.DISABLED

    async def test_disable_role_not_found(self) -> None:
        """禁用不存在的角色返回 NotFoundError。"""
        uow = FakeRbacUow()
        service = _make_service(uow)

        with pytest.raises(NotFoundError, match="不存在"):
            await service.disable_role(role_id=uuid4(), current_time=_NOW)  # type: ignore[attr-defined]


# ===========================================================================
# 权限分配测试
# ===========================================================================


class TestPermissionAssignment:
    """权限分配 Use Case 测试（SPEC §13.2、§13.3）。"""

    async def test_assign_permissions_as_super_admin(self) -> None:
        """超级管理员可以分配任意权限。"""
        uow = FakeRbacUow()
        actor = uuid4()
        super_role = _make_role(code="super", name="超管", is_super_admin=True)
        await uow.roles.add(super_role)
        await uow.user_roles.assign(user_id=actor, role_id=super_role.id)

        role = _make_role(code="editor", name="编辑")
        await uow.roles.add(role)
        service = _make_service(uow)

        await service.assign_permissions_to_role(  # type: ignore[attr-defined]
            role_id=role.id,
            permission_codes=frozenset({"system:user:read", "system:user:create"}),
            actor_id=actor,
            current_time=_NOW,
        )

        perms = await uow.role_permissions.get_for_role(role.id)
        assert "system:user:read" in perms
        assert "system:user:create" in perms

    async def test_assign_permissions_scope_denied(self) -> None:
        """普通管理员只能分配自身范围内的权限。"""
        uow = FakeRbacUow()
        actor = uuid4()
        normal_role = _make_role(code="admin", name="管理员")
        await uow.roles.add(normal_role)
        await uow.user_roles.assign(user_id=actor, role_id=normal_role.id)
        # 给操作者分配了 system:user:read 权限
        await uow.role_permissions.set_for_role(normal_role.id, frozenset({"system:user:read"}))

        target_role = _make_role(code="editor", name="编辑")
        await uow.roles.add(target_role)
        service = _make_service(uow)

        with pytest.raises(AuthorizationError):
            await service.assign_permissions_to_role(  # type: ignore[attr-defined]
                role_id=target_role.id,
                permission_codes=frozenset({"system:user:create"}),  # 超出范围
                actor_id=actor,
                current_time=_NOW,
            )


# ===========================================================================
# 用户-角色管理测试
# ===========================================================================


class TestUserRoleManagement:
    """用户-角色管理 Use Case 测试（SPEC §13.2、§13.4）。"""

    async def test_assign_roles_as_super_admin(self) -> None:
        """超级管理员可以分配角色。"""
        uow = FakeRbacUow()
        actor = uuid4()
        target = uuid4()
        super_role = _make_role(code="super", name="超管", is_super_admin=True)
        await uow.roles.add(super_role)
        await uow.user_roles.assign(user_id=actor, role_id=super_role.id)

        editor = _make_role(code="editor", name="编辑")
        await uow.roles.add(editor)
        service = _make_service(uow)

        await service.assign_roles_to_user(  # type: ignore[attr-defined]
            user_id=target,
            role_codes=frozenset({"editor"}),
            actor_id=actor,
            current_time=_NOW,
        )

        role_ids = await uow.user_roles.get_role_ids_for_user(target)
        assert editor.id in role_ids

    async def test_remove_roles_last_super_admin_protection(self) -> None:
        """移除最后一个超级管理员角色被拒绝（SPEC §13.4）。"""
        uow = FakeRbacUow()
        target = uuid4()
        super_role = _make_role(code="super", name="超管", is_super_admin=True)
        await uow.roles.add(super_role)
        await uow.user_roles.assign(user_id=target, role_id=super_role.id)
        uow.user_roles._super_admin_users.add(target)

        service = _make_service(uow)

        with pytest.raises(ConflictError, match="最后一个"):
            await service.remove_roles_from_user(  # type: ignore[attr-defined]
                user_id=target,
                role_codes=frozenset({"super"}),
                actor_id=uuid4(),
                current_time=_NOW,
            )


# ===========================================================================
# _is_super_admin_in_uow 测试
# ===========================================================================


class TestSuperAdminDetection:
    """超级管理员检测测试（SPEC §13.4）。"""

    async def test_super_admin_detected(self) -> None:
        """拥有超级管理员角色的用户被正确识别。"""
        uow = FakeRbacUow()
        user_id = uuid4()
        super_role = _make_role(code="super", name="超管", is_super_admin=True)
        await uow.roles.add(super_role)
        await uow.user_roles.assign(user_id=user_id, role_id=super_role.id)
        service = _make_service(uow)

        result = await service._is_super_admin_in_uow(uow, user_id)  # type: ignore[attr-defined, union-attr]
        assert result is True

    async def test_non_super_admin_detected(self) -> None:
        """普通用户不被识别为超级管理员。"""
        uow = FakeRbacUow()
        user_id = uuid4()
        normal_role = _make_role(code="admin", name="管理员")
        await uow.roles.add(normal_role)
        await uow.user_roles.assign(user_id=user_id, role_id=normal_role.id)
        service = _make_service(uow)

        result = await service._is_super_admin_in_uow(uow, user_id)  # type: ignore[attr-defined, union-attr]
        assert result is False

    async def test_user_without_roles_not_super(self) -> None:
        """无角色的用户不是超级管理员。"""
        uow = FakeRbacUow()
        service = _make_service(uow)

        result = await service._is_super_admin_in_uow(uow, uuid4())  # type: ignore[attr-defined, union-attr]
        assert result is False
