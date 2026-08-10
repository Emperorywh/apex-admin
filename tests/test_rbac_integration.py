"""RBAC 模块集成测试 — SPEC 5.6 / 5.7 / 13.1 / 13.2 / 13.3 / 18.2 / 25.2 / 28.2.

覆盖验收标准:
  - AC-0: 角色 API 契约全部通过（创建/详情/分页/更新/启用/禁用/
          分配权限点/查询成员/分配与移除用户角色；系统内置角色删除与禁用被拒绝）
  - AC-1: 分配不存在的权限点返回参数错误；被禁用角色的权限不再计入用户有效权限
  - AC-3: 角色、权限分配、用户角色变更写审计且与业务同事务
  - AC-4: 权限变更事务提交后下一受保护请求立即使用新权限关系（无 TTL 缓存）

使用真实 PostgreSQL（Testcontainers / 本地二进制），禁止 SQLite（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.application.context import UseCaseContext
from app.application.ports import Clock, IdGenerator
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.rbac.errors import (
    BuiltinRoleProtectedError,
    PermissionNotFoundError,
    RoleAlreadyActiveError,
    RoleAlreadyDisabledError,
    RoleNotFoundError,
    UserRoleNotAssignedError,
)
from app.modules.rbac.models import RoleStatus
from app.modules.rbac.schemas import (
    AssignPermissionsRequest,
    AssignUserRolesRequest,
    RoleCreateRequest,
    RoleUpdateRequest,
)
from app.modules.rbac.use_case import RbacUseCase

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


# ── 迁移辅助 ───────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from alembic import command

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_tables(database_url: str) -> None:
    """清理 RBAC、用户和审计表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM rbac_user_roles"))
            await conn.execute(text("DELETE FROM rbac_role_permissions"))
            await conn.execute(text("DELETE FROM rbac_roles"))
            await conn.execute(text("DELETE FROM rbac_permissions"))
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM users"))
    finally:
        await engine.dispose()


async def _seed_user(database_url: str) -> UUID:
    """创建测试用户并返回其 ID。"""

    user_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, "
                    "password_hash, status, created_at, updated_at) "
                    "VALUES (:id, :username, :dn, :ph, :st, :ca, :ua)",
                ),
                {
                    "id": str(user_id),
                    "username": f"testuser_{user_id.hex[:8]}",
                    "dn": "Test User",
                    "ph": "$argon2id$fake",
                    "st": "active",
                    "ca": now,
                    "ua": now,
                },
            )
    finally:
        await engine.dispose()
    return user_id


async def _count_audit_logs(database_url: str) -> int:
    """查询审计日志行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM audit_logs WHERE module = 'rbac'"),
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _count_permissions(database_url: str) -> int:
    """查询权限点行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM rbac_permissions"),
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


# ── 测试用辅助 ──────────────────────────────────────────────────────────────


class FixedClock(Clock):
    """固定时钟。"""

    def __init__(self, time: datetime) -> None:
        self._time = time

    def now(self) -> datetime:
        return self._time


class FixedIdGenerator(IdGenerator):
    """固定 ID 生成器。"""

    def __init__(self, *ids: UUID) -> None:
        self._ids = list(ids)
        self._n = 0

    def generate_id(self) -> UUID:
        if self._n < len(self._ids):
            result = self._ids[self._n]
            self._n += 1
            return result
        return uuid4()


def _make_use_case(  # type: ignore[no-untyped-def]
    engine: AsyncEngine,
    *,
    role_ids: tuple[UUID, ...] | None = None,
) -> tuple[RbacUseCase, UseCaseContext]:
    """构造测试用 RbacUseCase。"""

    from app.modules.audit.adapter import SqlAlchemyAuditRepository
    from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):
        return SqlAlchemyAuditRepository(session)

    def user_auth_port_factory(session):
        return SqlAlchemyUserAuthAdapter(session)

    ids = role_ids or (uuid4(),)
    return (
        RbacUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
            id_generator=FixedIdGenerator(*ids),
            audit_factory=audit_factory,
            user_auth_port_factory=user_auth_port_factory,
        ),
        UseCaseContext(request_id="test-req", actor_id="admin-001"),
    )


def _make_builtin_role_use_case(  # type: ignore[no-untyped-def]
    engine: AsyncEngine,
) -> tuple[RbacUseCase, UseCaseContext]:
    """构造测试用 Use Case，用于操作内置角色。

    内置角色通过直接插入数据库创建（模拟初始化器行为）。
    """

    return _make_use_case(engine)


async def _insert_builtin_role(
    database_url: str,
    *,
    code: str = "super_admin",
    display_name: str = "超级管理员",
) -> UUID:
    """直接插入内置角色到数据库（模拟初始化器行为）。"""

    role_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_roles "
                    "(id, code, display_name, description, status, "
                    "is_builtin, sort_order, created_at, updated_at) "
                    "VALUES (:id, :code, :dn, NULL, :st, TRUE, 0, :ca, :ua)",
                ),
                {
                    "id": str(role_id),
                    "code": code,
                    "dn": display_name,
                    "st": RoleStatus.ACTIVE.value,
                    "ca": now,
                    "ua": now,
                },
            )
    finally:
        await engine.dispose()
    return role_id


async def _insert_permission(
    database_url: str,
    *,
    code: str,
    module_code: str = "identity",
) -> UUID:
    """直接插入权限点到数据库。"""

    perm_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_permissions "
                    "(id, code, display_name, description, module_code, "
                    "is_active, created_at, updated_at) "
                    "VALUES (:id, :code, :code, NULL, :mc, TRUE, :ca, :ua)",
                ),
                {
                    "id": str(perm_id),
                    "code": code,
                    "mc": module_code,
                    "ca": now,
                    "ua": now,
                },
            )
    finally:
        await engine.dispose()
    return perm_id


async def _get_effective_permissions(
    database_url: str,
    user_id: UUID,
) -> set[str]:
    """通过 UserRbacPort 查询用户有效权限集。"""

    from app.modules.rbac.adapter import SqlAlchemyUserRbacAdapter

    engine = create_db_engine(database_url)
    try:
        async with SqlAlchemyUnitOfWork(engine) as uow:
            adapter = SqlAlchemyUserRbacAdapter(uow.session)
            return await adapter.get_effective_permission_codes(user_id)
    finally:
        await engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 角色 API 契约 — CRUD、启用/禁用、分配权限、查询成员、用户角色
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_role_create_and_detail(database_url: str) -> None:
    """创建角色并查询详情 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(engine, role_ids=(role_id,))

            # 创建角色

            result = await uc.create_role(
                ctx,
                RoleCreateRequest(
                    code="editor",
                    display_name="编辑者",
                    description="内容编辑",
                    sort_order=1,
                ),
            )
            assert result.code == "editor"
            assert result.display_name == "编辑者"

            # 查询详情
            detail = await uc.get_role_detail(ctx, role_id)
            assert detail.code == "editor"
            assert detail.permission_codes == []
            assert detail.member_count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_role_list_paginated(database_url: str) -> None:
    """分页查询角色列表 — SPEC 13.2 / 9.4."""

    from app.core.api.pagination import SortField, SortOrder

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(
                engine,
                role_ids=(uuid4(), uuid4(), uuid4()),
            )

            for code in ("alpha", "beta", "gamma"):
                await uc.create_role(
                    ctx,
                    RoleCreateRequest(code=code, display_name=code.upper()),
                )

            result = await uc.list_roles(
                ctx,
                page=1,
                page_size=2,
                sort_fields=[SortField(name="code", order=SortOrder.ASC)],
            )
            assert result["total"] == 3
            assert len(result["items"]) == 2  # type: ignore[arg-type]
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_role_update(database_url: str) -> None:
    """更新角色 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(engine, role_ids=(role_id, uuid4()))

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            result = await uc.update_role(
                ctx,
                role_id,
                RoleUpdateRequest(
                    display_name="高级编辑者",
                    description="更新后描述",
                    sort_order=5,
                ),
            )
            assert result.display_name == "高级编辑者"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_role_enable_disable(database_url: str) -> None:
    """启用和禁用角色 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4(), uuid4()),
            )

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 禁用
            disabled = await uc.disable_role(ctx, role_id)
            assert disabled.status == "disabled"

            # 再次禁用 → 冲突
            with pytest.raises(RoleAlreadyDisabledError):
                await uc.disable_role(ctx, role_id)

            # 启用
            enabled = await uc.enable_role(ctx, role_id)
            assert enabled.status == "active"

            # 再次启用 → 冲突
            with pytest.raises(RoleAlreadyActiveError):
                await uc.enable_role(ctx, role_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_builtin_role_delete_and_disable_rejected(
    database_url: str,
) -> None:
    """系统内置角色删除与禁用被拒绝 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        builtin_id = await _insert_builtin_role(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)

            # 禁用内置角色被拒绝
            with pytest.raises(BuiltinRoleProtectedError):
                await uc.disable_role(ctx, builtin_id)

            # 删除内置角色被拒绝
            with pytest.raises(BuiltinRoleProtectedError):
                await uc.delete_role(ctx, builtin_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_assign_permissions(database_url: str) -> None:
    """为角色分配权限点 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")
        await _insert_permission(database_url, code="system:user:write")

        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(engine, role_ids=(role_id, uuid4()))

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            result = await uc.assign_permissions(
                ctx,
                role_id,
                AssignPermissionsRequest(
                    permission_codes=[
                        "system:user:read",
                        "system:user:write",
                    ],
                ),
            )
            assert sorted(result.permission_codes) == [
                "system:user:read",
                "system:user:write",
            ]

            # 验证全量替换（移除一个权限）
            result = await uc.assign_permissions(
                ctx,
                role_id,
                AssignPermissionsRequest(
                    permission_codes=["system:user:read"],
                ),
            )
            assert result.permission_codes == ["system:user:read"]
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_assign_nonexistent_permission_returns_error(
    database_url: str,
) -> None:
    """分配不存在的权限点返回参数错误 — SPEC 13.2 / AC-1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")

        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(engine, role_ids=(role_id, uuid4()))

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            with pytest.raises(PermissionNotFoundError):
                await uc.assign_permissions(
                    ctx,
                    role_id,
                    AssignPermissionsRequest(
                        permission_codes=["nonexistent:perm:read"],
                    ),
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_query_role_members(database_url: str) -> None:
    """查询角色成员 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _seed_user(database_url)
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4()),
            )

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 分配用户角色
            await uc.assign_user_roles(
                ctx,
                user_id,
                AssignUserRolesRequest(role_codes=["editor"]),
            )

            # 查询成员
            members = await uc.get_role_members(
                ctx,
                role_id,
                page=1,
                page_size=10,
            )
            assert members["total"] == 1  # type: ignore[arg-type]
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_assign_and_remove_user_role(database_url: str) -> None:
    """分配与移除用户角色 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _seed_user(database_url)
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4(), uuid4()),
            )

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 分配角色
            result = await uc.assign_user_roles(
                ctx,
                user_id,
                AssignUserRolesRequest(role_codes=["editor"]),
            )
            assert result["added_count"] == 1  # type: ignore[arg-type]

            # 查询用户角色
            roles = await uc.get_user_roles(ctx, user_id)
            assert len(roles["role_ids"]) == 1  # type: ignore[arg-type]

            # 移除角色（全量替换为空）
            result = await uc.assign_user_roles(
                ctx,
                user_id,
                AssignUserRolesRequest(role_codes=[]),
            )
            assert result["removed_count"] == 1  # type: ignore[arg-type]

            # 验证已无角色
            roles = await uc.get_user_roles(ctx, user_id)
            assert len(roles["role_ids"]) == 0  # type: ignore[arg-type]
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_remove_user_role_direct(database_url: str) -> None:
    """通过 DELETE 移除单个用户角色 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _seed_user(database_url)
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4()),
            )

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )
            await uc.assign_user_roles(
                ctx,
                user_id,
                AssignUserRolesRequest(role_codes=["editor"]),
            )

            # 移除角色
            await uc.remove_user_role(ctx, user_id, role_id)

            # 再次移除 → 冲突
            with pytest.raises(UserRoleNotAssignedError):
                await uc.remove_user_role(ctx, user_id, role_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_role_code_conflict(database_url: str) -> None:
    """角色编码冲突 — SPEC 8.4 / 13.2."""

    from app.modules.rbac.errors import RoleAlreadyExistsError

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine, role_ids=(uuid4(), uuid4()))

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            with pytest.raises(RoleAlreadyExistsError):
                await uc.create_role(
                    ctx,
                    RoleCreateRequest(code="editor", display_name="另一个"),
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_role_not_found(database_url: str) -> None:
    """角色不存在返回 404 — SPEC 13.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            with pytest.raises(RoleNotFoundError):
                await uc.get_role_detail(ctx, uuid4())
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: 被禁用角色的权限不再计入用户有效权限
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_disabled_role_permissions_not_in_effective_set(
    database_url: str,
) -> None:
    """被禁用角色的权限不再计入用户有效权限集 — SPEC 13.1 / AC-1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _seed_user(database_url)
        await _insert_permission(database_url, code="system:user:read")
        await _insert_permission(database_url, code="system:user:write")

        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4(), uuid4(), uuid4()),
            )

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 分配权限
            await uc.assign_permissions(
                ctx,
                role_id,
                AssignPermissionsRequest(
                    permission_codes=["system:user:read", "system:user:write"],
                ),
            )

            # 分配用户角色
            await uc.assign_user_roles(
                ctx,
                user_id,
                AssignUserRolesRequest(role_codes=["editor"]),
            )

            # 验证启用角色的权限计入有效权限集
            effective = await _get_effective_permissions(database_url, user_id)
            assert effective == {"system:user:read", "system:user:write"}

            # 禁用角色
            await uc.disable_role(ctx, role_id)

            # 验证被禁用角色的权限不再计入有效权限集
            effective = await _get_effective_permissions(database_url, user_id)
            assert effective == set()
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 审计与业务同事务
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_role_operations_write_audit(database_url: str) -> None:
    """角色变更操作写审计且与业务同事务 — SPEC 18.2 / AC-3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4(), uuid4()),
            )

            audit_before = await _count_audit_logs(database_url)

            # 创建角色 — 应写审计
            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 更新角色 — 应写审计
            await uc.update_role(
                ctx,
                role_id,
                RoleUpdateRequest(display_name="高级编辑者"),
            )

            # 禁用角色 — 应写审计
            await uc.disable_role(ctx, role_id)

            # 启用角色 — 应写审计
            await uc.enable_role(ctx, role_id)

            audit_after = await _count_audit_logs(database_url)

            # 4 个操作各写一条审计记录
            assert audit_after - audit_before == 4
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_permission_assignment_writes_audit(database_url: str) -> None:
    """权限分配写审计 — SPEC 18.2 / AC-3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")

        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(engine, role_ids=(role_id, uuid4(), uuid4()))

            audit_before = await _count_audit_logs(database_url)

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )
            await uc.assign_permissions(
                ctx,
                role_id,
                AssignPermissionsRequest(
                    permission_codes=["system:user:read"],
                ),
            )

            audit_after = await _count_audit_logs(database_url)
            # create_role + assign_permissions = 2 条审计
            assert audit_after - audit_before == 2
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_user_role_assignment_writes_audit(database_url: str) -> None:
    """用户角色变更写审计 — SPEC 18.2 / AC-3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _seed_user(database_url)
        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4(), uuid4()),
            )

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            audit_before = await _count_audit_logs(database_url)

            await uc.assign_user_roles(
                ctx,
                user_id,
                AssignUserRolesRequest(role_codes=["editor"]),
            )

            await uc.remove_user_role(ctx, user_id, role_id)

            audit_after = await _count_audit_logs(database_url)
            # assign + remove = 2 条审计
            assert audit_after - audit_before == 2
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: 权限变更提交后立即生效（无 TTL 缓存）
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_permission_change_immediately_effective(database_url: str) -> None:
    """权限变更事务提交后下一请求立即使用新权限关系 — SPEC 13.3 / AC-4.

    无 TTL 缓存——每次查询都从数据库读取最新关系。
    """

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _seed_user(database_url)
        await _insert_permission(database_url, code="system:user:read")
        await _insert_permission(database_url, code="system:user:write")

        engine = create_db_engine(database_url)
        try:
            role_id = uuid4()
            uc, ctx = _make_use_case(
                engine,
                role_ids=(role_id, uuid4(), uuid4(), uuid4(), uuid4()),
            )

            await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 初始权限集为空
            effective = await _get_effective_permissions(database_url, user_id)
            assert effective == set()

            # 分配权限和用户角色
            await uc.assign_permissions(
                ctx,
                role_id,
                AssignPermissionsRequest(
                    permission_codes=["system:user:read"],
                ),
            )
            await uc.assign_user_roles(
                ctx,
                user_id,
                AssignUserRolesRequest(role_codes=["editor"]),
            )

            # 权限变更提交后立即生效（无 TTL 缓存）
            effective = await _get_effective_permissions(database_url, user_id)
            assert effective == {"system:user:read"}

            # 追加权限
            await uc.assign_permissions(
                ctx,
                role_id,
                AssignPermissionsRequest(
                    permission_codes=[
                        "system:user:read",
                        "system:user:write",
                    ],
                ),
            )

            # 立即看到新权限
            effective = await _get_effective_permissions(database_url, user_id)
            assert effective == {"system:user:read", "system:user:write"}

            # 移除全部权限
            await uc.assign_permissions(
                ctx,
                role_id,
                AssignPermissionsRequest(permission_codes=[]),
            )

            # 立即生效
            effective = await _get_effective_permissions(database_url, user_id)
            assert effective == set()
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# SPEC 25.2: 权限点目录同步
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_sync_permissions_idempotent(database_url: str) -> None:
    """连续两次同步权限点结果一致 — SPEC 25.2 / AC-2."""

    from app.modules.rbac.sync import collect_declared_permissions, sync_permissions

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            declared = collect_declared_permissions()

            # 第一次同步 — 应新增
            result1 = await sync_permissions(
                engine,
                declared_permissions=declared,
            )
            assert len(result1.added) > 0
            assert len(result1.updated) == 0

            # 第二次同步 — 应无变更
            result2 = await sync_permissions(
                engine,
                declared_permissions=declared,
            )
            assert len(result2.added) == 0
            assert len(result2.updated) == 0

            # 两次同步后数据库权限点数量一致
            assert result1.total_in_db == result2.total_in_db
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_sync_permissions_reports_orphans(database_url: str) -> None:
    """孤立权限点被报告但不被自动删除 — SPEC 25.2 / AC-2."""

    from app.modules.rbac.sync import collect_declared_permissions, sync_permissions

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        # 插入一个孤立权限点（不在任何模块声明中）
        await _insert_permission(database_url, code="orphan:perm:read")

        engine = create_db_engine(database_url)
        try:
            declared = collect_declared_permissions()
            assert "orphan:perm:read" not in declared

            # 同步（不清理）
            result = await sync_permissions(
                engine,
                declared_permissions=declared,
                clean_orphans=False,
            )

            # 孤立权限点被报告
            assert "orphan:perm:read" in result.orphaned

            # 孤立权限点未被删除
            perm_count = await _count_permissions(database_url)
            assert perm_count == result.total_in_db
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_sync_permissions_clean_orphans_with_confirm(
    database_url: str,
) -> None:
    """显式清理命令需确认标志 — SPEC 25.2 / AC-2."""

    from app.modules.rbac.sync import collect_declared_permissions, sync_permissions

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _insert_permission(database_url, code="orphan:perm:read")

        engine = create_db_engine(database_url)
        try:
            declared = collect_declared_permissions()

            # 清理孤立权限点
            result = await sync_permissions(
                engine,
                declared_permissions=declared,
                clean_orphans=True,
            )

            # 孤立权限点已清理
            assert "orphan:perm:read" in result.cleaned

            # 孤立权限点已从数据库删除
            perm_count = await _count_permissions(database_url)
            assert perm_count == result.total_in_db
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)
