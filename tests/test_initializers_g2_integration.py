"""身份与 RBAC 初始化器幂等集成测试 — SPEC 8.5 / 25.2.

覆盖验收标准:
  - AC-1: identity 与 rbac 初始化器（内置角色、基础权限点）
          重复执行不产生重复数据（集成测试）。

SPEC 8.5:
  - 初始化器使用稳定自然键执行幂等 upsert。
  - 初始化过程可重复执行且不会创建重复数据。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.core.initialization.framework import InitializationRunner
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.rbac.initializers import BuiltinRolesInitializer

# ── 迁移与清理辅助 ─────────────────────────────────────────────────────────


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
    """清理 RBAC 和相关表。"""

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


async def _count_builtin_roles(database_url: str) -> int:
    """查询内置角色数量。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM rbac_roles WHERE is_builtin = TRUE",
                ),
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _get_super_admin_role(database_url: str) -> dict[str, object] | None:
    """查询 super_admin 角色信息。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, code, display_name, is_builtin, status "
                    "FROM rbac_roles WHERE code = 'super_admin'",
                ),
            )
            row = result.first()
            if row is None:
                return None
            return {
                "id": row[0],
                "code": row[1],
                "display_name": row[2],
                "is_builtin": row[3],
                "status": row[4],
            }
    finally:
        await engine.dispose()


async def _count_permissions(database_url: str) -> int:
    """查询权限点总数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM rbac_permissions"),
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


# ── 内置角色初始化器测试 ──────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.integration
async def test_builtin_roles_initializer_creates_super_admin(
    database_url: str,
) -> None:
    """内置角色初始化器创建 super_admin 角色 — SPEC 8.5."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            runner = InitializationRunner([BuiltinRolesInitializer()])
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                await runner.run(uow.session)
                await uow.commit()
        finally:
            await engine.dispose()

        role = await _get_super_admin_role(database_url)
        assert role is not None
        assert role["code"] == "super_admin"
        assert role["is_builtin"] is True
        assert role["status"] == "active"
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_builtin_roles_initializer_idempotent(database_url: str) -> None:
    """内置角色初始化器重复执行不产生重复数据 — SPEC 8.5 / AC-1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            runner = InitializationRunner([BuiltinRolesInitializer()])

            # 第一次执行
            uow1 = SqlAlchemyUnitOfWork(engine)
            async with uow1:
                await runner.run(uow1.session)
                await uow1.commit()

            count_after_first = await _count_builtin_roles(database_url)
            assert count_after_first == 1

            # 第二次执行
            uow2 = SqlAlchemyUnitOfWork(engine)
            async with uow2:
                await runner.run(uow2.session)
                await uow2.commit()

            count_after_second = await _count_builtin_roles(database_url)
            assert count_after_second == 1  # 不产生重复

            # 第三次执行
            uow3 = SqlAlchemyUnitOfWork(engine)
            async with uow3:
                await runner.run(uow3.session)
                await uow3.commit()

            count_after_third = await _count_builtin_roles(database_url)
            assert count_after_third == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_builtin_roles_initializer_updates_on_rerun(
    database_url: str,
) -> None:
    """内置角色初始化器重复执行时更新而非插入（upsert）— SPEC 8.5."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        try:
            runner = InitializationRunner([BuiltinRolesInitializer()])

            # 第一次执行
            uow1 = SqlAlchemyUnitOfWork(engine)
            async with uow1:
                await runner.run(uow1.session)
                await uow1.commit()

            # 手动修改 display_name 验证 upsert 会恢复
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE rbac_roles SET display_name = '修改过' "
                        "WHERE code = 'super_admin'",
                    ),
                )

            # 第二次执行 — upsert 应恢复 display_name
            uow2 = SqlAlchemyUnitOfWork(engine)
            async with uow2:
                await runner.run(uow2.session)
                await uow2.commit()

            role = await _get_super_admin_role(database_url)
            assert role is not None
            assert role["display_name"] == "超级管理员"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ── 权限点同步幂等性测试 ──────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.integration
async def test_permission_sync_idempotent(database_url: str) -> None:
    """权限点同步重复执行不产生重复数据 — SPEC 8.5 / 25.2 / AC-1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        from app.modules.rbac.sync import (
            collect_declared_permissions,
            sync_permissions,
        )

        declared = collect_declared_permissions()
        assert len(declared) > 0  # 确保有声明的权限点

        # 第一次同步
        result1 = await sync_permissions(
            create_db_engine(database_url),
            declared_permissions=declared,
        )
        assert len(result1.added) > 0  # 第一次新增了权限点

        count_after_first = await _count_permissions(database_url)
        assert count_after_first == len(declared)

        # 第二次同步 — 不应产生重复
        result2 = await sync_permissions(
            create_db_engine(database_url),
            declared_permissions=declared,
        )
        assert len(result2.added) == 0  # 无新增

        count_after_second = await _count_permissions(database_url)
        assert count_after_second == count_after_first  # 数量不变
    finally:
        await _cleanup_tables(database_url)
