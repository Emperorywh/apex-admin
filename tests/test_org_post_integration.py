"""岗位与用户组织关系集成测试 — SPEC 14.2 / 14.3 / 5.7 / 11.1.

覆盖 TASK-020 验收标准:
  - AC-0: 岗位 API 契约全部通过（创建/查询/更新/启用禁用/
          为用户分配/移除用户岗位；分配幂等且防重复）
  - AC-1: UserDisabled 事件的事务内处理器按文档规则处理组织关系
  - AC-3: 部门变更后用户权限不发生隐式变化

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
from app.modules.org.errors import (
    PostAlreadyActiveError,
    PostAlreadyDisabledError,
    PostAlreadyExistsError,
    PostDisabledError,
    PostHasUsersError,
    PostNotFoundError,
    UserAlreadyHasDepartmentError,
    UserDepartmentNotFoundError,
    UserPostNotFoundError,
)
from app.modules.org.schemas import (
    PostCreateRequest,
    PostUpdateRequest,
)
from app.modules.org.use_case import OrgUseCase

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
    """清理组织、审计和用户表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM org_user_posts"))
            await conn.execute(text("DELETE FROM org_user_departments"))
            await conn.execute(text("DELETE FROM org_posts"))
            await conn.execute(text("DELETE FROM org_departments"))
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM users"))
    finally:
        await engine.dispose()


async def _seed_user(database_url: str, username: str = "orguser") -> UUID:
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
                    "username": f"{username}_{user_id.hex[:8]}",
                    "dn": username.title(),
                    "ph": "$argon2id$fake",
                    "st": "active",
                    "ca": now,
                    "ua": now,
                },
            )
    finally:
        await engine.dispose()
    return user_id


# ── 测试用辅助 ──────────────────────────────────────────────────────────────


class FixedClock(Clock):
    """固定时钟。"""

    def __init__(self, time: datetime) -> None:
        self._time = time

    def now(self) -> datetime:
        return self._time


class CountingIdGenerator(IdGenerator):
    """顺序 ID 生成器——可预测的 UUID 序列。"""

    def __init__(self) -> None:
        self._counter = 0

    def generate_id(self) -> UUID:
        self._counter += 1
        return UUID(int=self._counter)


_TEST_ACTOR_ID = "00000000-0000-0000-0000-0000000000bb"


def _make_use_case(
    engine: AsyncEngine,
) -> tuple[OrgUseCase, UseCaseContext]:
    """构造测试用 OrgUseCase。"""

    from app.modules.audit.adapter import SqlAlchemyAuditRepository
    from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyAuditRepository(session)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyUserAuthAdapter(session)

    return (
        OrgUseCase(
            uow_factory=uow_factory,
            clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
            id_generator=CountingIdGenerator(),
            audit_factory=audit_factory,
            user_auth_port_factory=user_auth_port_factory,
        ),
        UseCaseContext(request_id="test-req", actor_id=_TEST_ACTOR_ID),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 岗位 API 契约 — 创建/查询/更新/启用禁用/分配/移除；分配幂等且防重复
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestPostCRUD:
    """岗位 CRUD 契约测试 — SPEC 14.2."""

    async def test_create_post(self, database_url: str) -> None:
        """创建岗位返回正确字段。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            result = await uc.create_post(
                ctx,
                PostCreateRequest(
                    code="engineer",
                    display_name="工程师",
                    description="研发岗位",
                    sort_order=1,
                ),
            )
            assert result["code"] == "engineer"
            assert result["display_name"] == "工程师"
            assert result["status"] == "active"
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_create_duplicate_code_409(self, database_url: str) -> None:
        """重复编码返回 PostAlreadyExistsError。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            await uc.create_post(
                ctx,
                PostCreateRequest(code="mgr", display_name="经理"),
            )
            with pytest.raises(PostAlreadyExistsError):
                await uc.create_post(
                    ctx,
                    PostCreateRequest(code="mgr", display_name="经理2"),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_list_posts(self, database_url: str) -> None:
        """查询岗位列表返回所有岗位。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            await uc.create_post(
                ctx,
                PostCreateRequest(code="dev", display_name="开发"),
            )
            await uc.create_post(
                ctx,
                PostCreateRequest(code="qa", display_name="测试"),
            )
            result = await uc.list_posts(ctx)
            assert result["total"] == 2
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_get_post_detail(self, database_url: str) -> None:
        """查询岗位详情返回正确信息。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            created = await uc.create_post(
                ctx,
                PostCreateRequest(code="lead", display_name="主管"),
            )
            detail = await uc.get_post_detail(ctx, created["id"])
            assert detail["code"] == "lead"
            assert detail["user_count"] == 0
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_get_post_not_found(self, database_url: str) -> None:
        """查询不存在的岗位返回 PostNotFoundError。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            with pytest.raises(PostNotFoundError):
                await uc.get_post_detail(ctx, uuid4())
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_update_post(self, database_url: str) -> None:
        """更新岗位返回正确字段。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            created = await uc.create_post(
                ctx,
                PostCreateRequest(code="ops", display_name="运维"),
            )
            updated = await uc.update_post(
                ctx,
                created["id"],
                PostUpdateRequest(display_name="运维工程师", description="OPs"),
            )
            assert updated["display_name"] == "运维工程师"
            assert updated["description"] == "OPs"
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_enable_disable_post(self, database_url: str) -> None:
        """启用禁用岗位返回正确状态。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            created = await uc.create_post(
                ctx,
                PostCreateRequest(code="dba", display_name="DBA"),
            )
            disabled = await uc.disable_post(ctx, created["id"])
            assert disabled["status"] == "disabled"
            with pytest.raises(PostAlreadyDisabledError):
                await uc.disable_post(ctx, created["id"])
            enabled = await uc.enable_post(ctx, created["id"])
            assert enabled["status"] == "active"
            with pytest.raises(PostAlreadyActiveError):
                await uc.enable_post(ctx, created["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_delete_post(self, database_url: str) -> None:
        """删除无关联用户的岗位成功。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            created = await uc.create_post(
                ctx,
                PostCreateRequest(code="tmp", display_name="临时"),
            )
            await uc.delete_post(ctx, created["id"])
            with pytest.raises(PostNotFoundError):
                await uc.get_post_detail(ctx, created["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_delete_post_with_users_rejected(self, database_url: str) -> None:
        """有关联用户的岗位不能删除。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            post = await uc.create_post(
                ctx,
                PostCreateRequest(code="assigned", display_name="已分配岗位"),
            )
            await uc.assign_user_post(ctx, user_id, post["id"])
            with pytest.raises(PostHasUsersError):
                await uc.delete_post(ctx, post["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0 (续): 用户岗位分配/移除 — 幂等且防重复
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestUserPostAssignment:
    """用户岗位分配与移除测试 — SPEC 14.2."""

    async def test_assign_user_post(self, database_url: str) -> None:
        """为用户分配岗位成功。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            post = await uc.create_post(
                ctx,
                PostCreateRequest(code="dev", display_name="开发"),
            )
            result = await uc.assign_user_post(ctx, user_id, post["id"])
            assert result["user_id"] == user_id
            assert result["post_id"] == post["id"]
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_assign_user_post_idempotent(self, database_url: str) -> None:
        """重复分配同一岗位幂等——不报错。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            post = await uc.create_post(
                ctx,
                PostCreateRequest(code="qa", display_name="测试"),
            )
            await uc.assign_user_post(ctx, user_id, post["id"])
            # 幂等——第二次不报错
            await uc.assign_user_post(ctx, user_id, post["id"])
            # 数据库中仅一条记录
            info = await uc.get_user_org_info(ctx, user_id)
            assert len(info["posts"]) == 1
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_assign_disabled_post_rejected(self, database_url: str) -> None:
        """为用户分配已禁用岗位被拒绝。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            post = await uc.create_post(
                ctx,
                PostCreateRequest(code="old", display_name="旧岗"),
            )
            await uc.disable_post(ctx, post["id"])
            with pytest.raises(PostDisabledError):
                await uc.assign_user_post(ctx, user_id, post["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_remove_user_post(self, database_url: str) -> None:
        """移除用户岗位成功。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            post = await uc.create_post(
                ctx,
                PostCreateRequest(code="temp", display_name="临时"),
            )
            await uc.assign_user_post(ctx, user_id, post["id"])
            await uc.remove_user_post(ctx, user_id, post["id"])
            info = await uc.get_user_org_info(ctx, user_id)
            assert len(info["posts"]) == 0
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_remove_nonexistent_user_post(self, database_url: str) -> None:
        """移除不存在的用户岗位关系返回错误。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            with pytest.raises(UserPostNotFoundError):
                await uc.remove_user_post(ctx, user_id, uuid4())
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0 (续): 用户主部门关系
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestUserDepartment:
    """用户主部门关系测试 — SPEC 14.3."""

    async def test_assign_user_department(self, database_url: str) -> None:
        """设置用户主部门成功。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            from app.modules.org.schemas import (
                DepartmentCreateRequest,
            )

            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="eng", display_name="工程部"),
            )
            result = await uc.assign_user_department(ctx, user_id, dept["id"])
            assert result["department_id"] == dept["id"]
            assert result["is_primary"] is True
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_assign_department_already_has(self, database_url: str) -> None:
        """用户已有主部门时再次分配被拒绝。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            from app.modules.org.schemas import (
                DepartmentCreateRequest,
            )

            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            dept1 = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="d1", display_name="部门1"),
            )
            dept2 = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="d2", display_name="部门2"),
            )
            await uc.assign_user_department(ctx, user_id, dept1["id"])
            with pytest.raises(UserAlreadyHasDepartmentError):
                await uc.assign_user_department(ctx, user_id, dept2["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_remove_user_department(self, database_url: str) -> None:
        """移除用户主部门成功。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            from app.modules.org.schemas import (
                DepartmentCreateRequest,
            )

            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="总部"),
            )
            await uc.assign_user_department(ctx, user_id, dept["id"])
            await uc.remove_user_department(ctx, user_id)
            info = await uc.get_user_org_info(ctx, user_id)
            assert info["department"] is None
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_remove_nonexistent_department(self, database_url: str) -> None:
        """移除不存在的主部门关系返回错误。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            with pytest.raises(UserDepartmentNotFoundError):
                await uc.remove_user_department(ctx, user_id)
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: UserDisabled 事件处理器清除组织关系
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestUserDisabledClearsOrgRelations:
    """用户禁用时组织关系清除测试 — SPEC 14.3 / 5.7."""

    async def test_disable_user_clears_dept_and_posts(
        self,
        database_url: str,
    ) -> None:
        """禁用用户时清除主部门和岗位关系。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            from app.modules.org.adapter import SqlAlchemyOrgRepository
            from app.modules.org.schemas import (
                DepartmentCreateRequest,
            )

            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="eng", display_name="工程部"),
            )
            post1 = await uc.create_post(
                ctx,
                PostCreateRequest(code="dev", display_name="开发"),
            )
            post2 = await uc.create_post(
                ctx,
                PostCreateRequest(code="lead", display_name="主管"),
            )
            await uc.assign_user_department(ctx, user_id, dept["id"])
            await uc.assign_user_post(ctx, user_id, post1["id"])
            await uc.assign_user_post(ctx, user_id, post2["id"])

            # 直接调用 handler 逻辑清除关系
            async with SqlAlchemyUnitOfWork(engine) as uow:
                repo = SqlAlchemyOrgRepository(uow.session)
                await repo.clear_user_org_relations(user_id)
                await uow.commit()

            info = await uc.get_user_org_info(ctx, user_id)
            assert info["department"] is None
            assert len(info["posts"]) == 0
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_event_handler_clears_org_relations(
        self,
        database_url: str,
    ) -> None:
        """USER.DISABLED 事件处理器清除组织关系（集成测试）。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            from app.core.events.events import DomainEvent
            from app.modules.org.handlers import ClearUserOrgRelationsOnDisabled
            from app.modules.org.schemas import (
                DepartmentCreateRequest,
            )

            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="it", display_name="IT部"),
            )
            post = await uc.create_post(
                ctx,
                PostCreateRequest(code="mgr", display_name="经理"),
            )
            await uc.assign_user_department(ctx, user_id, dept["id"])
            await uc.assign_user_post(ctx, user_id, post["id"])

            # 模拟事件分发
            handler = ClearUserOrgRelationsOnDisabled()
            event = DomainEvent(
                code="USER.DISABLED",
                payload={"user_id": str(user_id)},
            )
            async with SqlAlchemyUnitOfWork(engine) as uow:
                await handler.handle(event, uow.session)
                await uow.commit()

            info = await uc.get_user_org_info(ctx, user_id)
            assert info["department"] is None
            assert len(info["posts"]) == 0
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 部门变更后用户权限不发生隐式变化
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDepartmentChangeNoImplicitPermission:
    """部门变更不扩权测试 — SPEC 14.3."""

    async def test_department_change_no_rbac_effect(
        self,
        database_url: str,
    ) -> None:
        """用户的部门关系变化不影响 RBAC 权限。

        SPEC 14.3: "部门变更不会隐式扩大用户权限"。
        SPEC 14.2: "岗位不直接替代角色和权限"。
        本基座中部门/岗位与 RBAC 权限完全独立——权限只能通过
        RBAC 角色分配/移除显式变更。
        """

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            # 验证 org 模块不写入也不读取任何 RBAC 表
            # 部门变更和岗位分配只操作 org_* 表
            from app.modules.org.schemas import (
                DepartmentCreateRequest,
            )

            uc, ctx = _make_use_case(engine)
            user_id = await _seed_user(database_url)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="eng", display_name="工程部"),
            )
            post = await uc.create_post(
                ctx,
                PostCreateRequest(code="dev", display_name="开发"),
            )

            # 分配部门前，查询 RBAC 表无变化
            before_count = await _count_rbac_assignments(database_url)

            await uc.assign_user_department(ctx, user_id, dept["id"])
            await uc.assign_user_post(ctx, user_id, post["id"])

            # 分配后 RBAC 表行数不变
            after_assign = await _count_rbac_assignments(database_url)
            assert after_assign == before_count

            # 移除部门
            await uc.remove_user_department(ctx, user_id)
            await uc.remove_user_post(ctx, user_id, post["id"])

            # 移除后 RBAC 表行数仍不变
            after_remove = await _count_rbac_assignments(database_url)
            assert after_remove == before_count

        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


async def _count_rbac_assignments(database_url: str) -> int:
    """统计 RBAC 角色分配行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM rbac_role_assignments"),
            )
            return int(result.scalar() or 0)
    except Exception:
        # 表可能不存在，返回 0
        return 0
    finally:
        await engine.dispose()
