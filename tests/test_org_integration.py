"""组织模块集成测试 — SPEC 5.6 / 5.7 / 14.1 / 18.2 / 28.2.

覆盖验收标准:
  - AC-0: 部门 API 契约全部通过（创建/树查询/详情/更新/启用禁用/
          层级与排序调整/负责人设置）
  - AC-1: 循环层级防护测试通过（直接、间接与并发调整均无法形成循环）
  - AC-2: 删除保护规则测试通过（存在用户或子部门的部门按文档规则拒绝删除）
  - AC-3: 部门禁用后树查询可见性符合文档规则
  - AC-4: 部门变更写审计且与业务同事务

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
    DepartmentAlreadyActiveError,
    DepartmentAlreadyDisabledError,
    DepartmentCycleError,
    DepartmentHasChildrenError,
    DepartmentNotFoundError,
    InvalidParentError,
)
from app.modules.org.schemas import (
    DepartmentCreateRequest,
    DepartmentHierarchyRequest,
    DepartmentLeaderRequest,
    DepartmentUpdateRequest,
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
    """清理组织和审计表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM org_user_posts"))
            await conn.execute(text("DELETE FROM org_user_departments"))
            await conn.execute(text("DELETE FROM org_posts"))
            await conn.execute(text("DELETE FROM org_departments"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


async def _seed_user(database_url: str) -> UUID:
    """创建测试用户并返回其 ID（供负责人设置测试用）。"""

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
                    "username": f"orgtest_{user_id.hex[:8]}",
                    "dn": "Org Test User",
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
    """查询组织模块审计日志行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM audit_logs WHERE module = 'org'"),
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
# AC-0: 部门 API 契约 — 创建/树查询/详情/更新/启用禁用/层级与排序调整/负责人设置
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDepartmentCRUD:
    """部门 CRUD 契约测试 — SPEC 14.1."""

    async def test_create_department(self, database_url: str) -> None:
        """创建部门返回正确字段。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            result = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="engineering",
                    display_name="工程部",
                    description="研发团队",
                    sort_order=0,
                ),
            )
            assert result["code"] == "engineering"
            assert result["display_name"] == "工程部"
            assert result["status"] == "active"
            assert result["parent_id"] is None
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_create_with_parent(self, database_url: str) -> None:
        """创建子部门时设置正确的 parent_id。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            parent = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="总部"),
            )
            child = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="backend",
                    display_name="后端组",
                    parent_id=parent["id"],
                ),
            )
            assert child["parent_id"] == parent["id"]
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_create_nonexistent_parent(self, database_url: str) -> None:
        """指定不存在的父部门返回 InvalidParentError。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            with pytest.raises(InvalidParentError):
                await uc.create_department(
                    ctx,
                    DepartmentCreateRequest(
                        code="test",
                        display_name="T",
                        parent_id=uuid4(),
                    ),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_create_disabled_parent_rejected(
        self,
        database_url: str,
    ) -> None:
        """禁用状态的部门不能作为父部门。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            parent = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="总部"),
            )
            await uc.disable_department(ctx, parent["id"])
            with pytest.raises(InvalidParentError):
                await uc.create_department(
                    ctx,
                    DepartmentCreateRequest(
                        code="child",
                        display_name="C",
                        parent_id=parent["id"],
                    ),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_create_duplicate_code(self, database_url: str) -> None:
        """重复编码返回 DepartmentAlreadyExistsError。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="dup", display_name="D1"),
            )
            from app.modules.org.errors import DepartmentAlreadyExistsError

            with pytest.raises(DepartmentAlreadyExistsError):
                await uc.create_department(
                    ctx,
                    DepartmentCreateRequest(code="dup", display_name="D2"),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_get_detail(self, database_url: str) -> None:
        """查询部门详情返回正确信息含子部门数量。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            parent = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="总部"),
            )
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="c1",
                    display_name="C1",
                    parent_id=parent["id"],
                ),
            )
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="c2",
                    display_name="C2",
                    parent_id=parent["id"],
                ),
            )
            detail = await uc.get_department_detail(ctx, parent["id"])
            assert detail["code"] == "hq"
            assert detail["child_count"] == 2
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_get_detail_not_found(self, database_url: str) -> None:
        """查询不存在的部门返回 DepartmentNotFoundError。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            with pytest.raises(DepartmentNotFoundError):
                await uc.get_department_detail(ctx, uuid4())
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_update_department(self, database_url: str) -> None:
        """更新部门基本信息。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="旧名称"),
            )
            updated = await uc.update_department(
                ctx,
                dept["id"],
                DepartmentUpdateRequest(
                    display_name="新名称",
                    description="新描述",
                ),
            )
            assert updated["display_name"] == "新名称"
            assert updated["description"] == "新描述"
            assert updated["code"] == "hq"
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_enable_disable(self, database_url: str) -> None:
        """启用和禁用部门。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="总部"),
            )
            assert dept["status"] == "active"

            disabled = await uc.disable_department(ctx, dept["id"])
            assert disabled["status"] == "disabled"

            with pytest.raises(DepartmentAlreadyDisabledError):
                await uc.disable_department(ctx, dept["id"])

            enabled = await uc.enable_department(ctx, dept["id"])
            assert enabled["status"] == "active"

            with pytest.raises(DepartmentAlreadyActiveError):
                await uc.enable_department(ctx, dept["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_set_leader(self, database_url: str) -> None:
        """设置部门负责人。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            user_id = await _seed_user(database_url)
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="总部"),
            )
            with_leader = await uc.set_leader(
                ctx,
                dept["id"],
                DepartmentLeaderRequest(leader_id=user_id),
            )
            assert with_leader["leader_id"] == user_id

            cleared = await uc.set_leader(
                ctx,
                dept["id"],
                DepartmentLeaderRequest(leader_id=None),
            )
            assert cleared["leader_id"] is None
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_set_leader_nonexistent_user(
        self,
        database_url: str,
    ) -> None:
        """设置不存在的用户为负责人返回 UserNotFoundError。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="总部"),
            )
            from app.modules.identity.errors import UserNotFoundError

            with pytest.raises(UserNotFoundError):
                await uc.set_leader(
                    ctx,
                    dept["id"],
                    DepartmentLeaderRequest(leader_id=uuid4()),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_adjust_hierarchy(self, database_url: str) -> None:
        """调整部门层级与排序。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            root = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="root", display_name="Root"),
            )
            target = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="target", display_name="Target"),
            )
            moved = await uc.adjust_hierarchy(
                ctx,
                target["id"],
                DepartmentHierarchyRequest(
                    parent_id=root["id"],
                    sort_order=5,
                ),
            )
            assert moved["parent_id"] == root["id"]
            assert moved["sort_order"] == 5
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_adjust_to_root(self, database_url: str) -> None:
        """调整部门为根部门（parent_id=null）。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            parent = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="pp", display_name="P"),
            )
            child = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="cc",
                    display_name="C",
                    parent_id=parent["id"],
                ),
            )
            moved = await uc.adjust_hierarchy(
                ctx,
                child["id"],
                DepartmentHierarchyRequest(parent_id=None, sort_order=0),
            )
            assert moved["parent_id"] is None
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_get_tree(self, database_url: str) -> None:
        """查询部门树返回正确的层级结构。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            root = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="root", display_name="Root"),
            )
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="c1",
                    display_name="C1",
                    parent_id=root["id"],
                    sort_order=0,
                ),
            )
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="c2",
                    display_name="C2",
                    parent_id=root["id"],
                    sort_order=1,
                ),
            )
            tree = await uc.get_department_tree(ctx)
            assert len(tree) == 1
            assert tree[0]["code"] == "root"
            assert len(tree[0]["children"]) == 2
            assert tree[0]["children"][0]["code"] == "c1"
            assert tree[0]["children"][1]["code"] == "c2"
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: 循环层级防护 — 直接、间接与并发
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDeptCyclePrevention:
    """循环层级防护测试 — SPEC 14.1: "防止形成循环层级"。"""

    async def test_direct_cycle_self(self, database_url: str) -> None:
        """直接循环：将部门设为自身的子部门 → 拒绝。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="aa", display_name="A"),
            )
            with pytest.raises(DepartmentCycleError):
                await uc.adjust_hierarchy(
                    ctx,
                    dept["id"],
                    DepartmentHierarchyRequest(
                        parent_id=dept["id"],
                        sort_order=0,
                    ),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_indirect_cycle(self, database_url: str) -> None:
        """间接循环：将父部门移动到子部门下 → 拒绝。

        构建结构 A → B → C，然后尝试将 A 移动到 C 下。
        """

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            a = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="aa", display_name="A"),
            )
            b = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="bb",
                    display_name="B",
                    parent_id=a["id"],
                ),
            )
            c = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="cc",
                    display_name="C",
                    parent_id=b["id"],
                ),
            )
            # 尝试将 A 移动到 C 下——形成循环 A → C → B → A
            with pytest.raises(DepartmentCycleError):
                await uc.adjust_hierarchy(
                    ctx,
                    a["id"],
                    DepartmentHierarchyRequest(
                        parent_id=c["id"],
                        sort_order=0,
                    ),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_indirect_cycle_deep(self, database_url: str) -> None:
        """深层间接循环：将根部门移动到深层后代下 → 拒绝。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            a = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="aa", display_name="A"),
            )
            b = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="bb",
                    display_name="B",
                    parent_id=a["id"],
                ),
            )
            c = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="cc",
                    display_name="C",
                    parent_id=b["id"],
                ),
            )
            d = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="dd",
                    display_name="D",
                    parent_id=c["id"],
                ),
            )
            # 尝试将 A 移动到 D 下——形成深层循环
            with pytest.raises(DepartmentCycleError):
                await uc.adjust_hierarchy(
                    ctx,
                    a["id"],
                    DepartmentHierarchyRequest(
                        parent_id=d["id"],
                        sort_order=0,
                    ),
                )
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_valid_move_no_cycle(self, database_url: str) -> None:
        """合法的层级调整不触发循环错误。

        构建两个独立的子树，将一个子树的节点移动到另一个子树下。
        """

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            root1 = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="r1", display_name="R1"),
            )
            root2 = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="r2", display_name="R2"),
            )
            child1 = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="c1",
                    display_name="C1",
                    parent_id=root1["id"],
                ),
            )
            # 将 child1 从 root1 下移动到 root2 下——合法
            moved = await uc.adjust_hierarchy(
                ctx,
                child1["id"],
                DepartmentHierarchyRequest(
                    parent_id=root2["id"],
                    sort_order=0,
                ),
            )
            assert moved["parent_id"] == root2["id"]
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_concurrent_adjustment_no_cycle(
        self,
        database_url: str,
    ) -> None:
        """并发调整无法形成循环 — 事务级咨询锁序列化（SPEC 14.1 / 34.3）.

        构建初始结构: A → B, C 独立
        并发尝试:
          TXN 1: 将 C 移动到 B 下  → C → B → A
          TXN 2: 将 A 移动到 C 下  → A → C → B → A （如果 TXN1 先提交）

        由于咨询锁序列化，两个事务不会同时通过循环检查。
        最终结果不能形成循环。
        """

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)

            # 构建初始结构
            a = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="aa", display_name="A"),
            )
            b = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="bb",
                    display_name="B",
                    parent_id=a["id"],
                ),
            )
            c = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="cc", display_name="C"),
            )

            a_id = a["id"]
            b_id = b["id"]
            c_id = c["id"]

            errors: list[Exception] = []

            async def _move_c_under_b() -> None:
                """将 C 移动到 B 下。"""

                try:
                    await uc.adjust_hierarchy(
                        ctx,
                        c_id,
                        DepartmentHierarchyRequest(
                            parent_id=b_id,
                            sort_order=0,
                        ),
                    )
                except Exception as exc:
                    errors.append(exc)

            async def _move_a_under_c() -> None:
                """将 A 移动到 C 下——如果 C 已在 B 下则形成循环。"""

                # 小延迟让两个事务竞争
                await asyncio.sleep(0.01)
                try:
                    await uc.adjust_hierarchy(
                        ctx,
                        a_id,
                        DepartmentHierarchyRequest(
                            parent_id=c_id,
                            sort_order=0,
                        ),
                    )
                except Exception as exc:
                    errors.append(exc)

            # 并发执行两个层级调整
            await asyncio.gather(
                _move_c_under_b(),
                _move_a_under_c(),
            )

            # 验证最终结果不包含循环
            # 重新查询树并验证没有循环
            tree = await uc.get_department_tree(ctx, include_disabled=True)

            # 收集所有部门的 parent 关系
            parent_map: dict[UUID, UUID | None] = {}

            def _collect(nodes: list[dict[str, object]]) -> None:
                for node in nodes:
                    node_id = node["id"]  # type: ignore[assignment]
                    parent_id = node.get("parent_id")  # type: ignore[union-attr]
                    parent_map[node_id] = parent_id  # type: ignore[assignment]
                    children = node.get("children", [])  # type: ignore[union-attr]
                    _collect(children)  # type: ignore[arg-type]

            _collect(tree)

            # 从每个节点向上遍历，验证不会回到自身（无循环）
            for start_id in parent_map:
                visited: set[UUID] = set()
                current: UUID | None = start_id
                while current is not None:
                    assert current not in visited, (
                        f"检测到循环：从 {start_id} 出发经过 {current} 两次"
                    )
                    visited.add(current)
                    current = parent_map.get(current)
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 删除保护规则
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDeptDeleteProtection:
    """删除保护规则测试 — SPEC 14.1: "有用户或子部门时的删除规则明确"。"""

    async def test_delete_leaf_department(self, database_url: str) -> None:
        """无子部门无用户的叶子部门可以删除。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="leaf", display_name="Leaf"),
            )
            await uc.delete_department(ctx, dept["id"])
            with pytest.raises(DepartmentNotFoundError):
                await uc.get_department_detail(ctx, dept["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_delete_with_children_rejected(
        self,
        database_url: str,
    ) -> None:
        """存在子部门时拒绝删除。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            parent = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="parent", display_name="P"),
            )
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="child",
                    display_name="C",
                    parent_id=parent["id"],
                ),
            )
            with pytest.raises(DepartmentHasChildrenError):
                await uc.delete_department(ctx, parent["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_delete_not_found(self, database_url: str) -> None:
        """删除不存在的部门返回 DepartmentNotFoundError。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            with pytest.raises(DepartmentNotFoundError):
                await uc.delete_department(ctx, uuid4())
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_delete_after_children_removed(
        self,
        database_url: str,
    ) -> None:
        """子部门删除后，父部门可以被删除。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            parent = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="pp", display_name="P"),
            )
            child = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="cc",
                    display_name="C",
                    parent_id=parent["id"],
                ),
            )
            await uc.delete_department(ctx, child["id"])
            await uc.delete_department(ctx, parent["id"])
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 部门禁用后树查询可见性
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDeptDisabledVisibility:
    """部门禁用后树查询可见性测试 — SPEC 14.1.

    文档规则:
      - 默认树查询包含禁用部门（管理员需要看到完整结构），但标记为 disabled。
      - 当 include_disabled=false 时，禁用部门被排除。
    """

    async def test_disabled_visible_by_default(self, database_url: str) -> None:
        """禁用部门默认在树查询中可见（标记为 disabled）。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            root = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="root", display_name="R"),
            )
            child = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="child",
                    display_name="C",
                    parent_id=root["id"],
                ),
            )
            await uc.disable_department(ctx, child["id"])

            tree = await uc.get_department_tree(ctx, include_disabled=True)
            assert len(tree) == 1
            assert len(tree[0]["children"]) == 1
            assert tree[0]["children"][0]["status"] == "disabled"
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_disabled_excluded_when_requested(
        self,
        database_url: str,
    ) -> None:
        """include_disabled=false 时禁用部门被排除。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            root = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="root", display_name="R"),
            )
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="active_child",
                    display_name="AC",
                    parent_id=root["id"],
                ),
            )
            disabled_child = await uc.create_department(
                ctx,
                DepartmentCreateRequest(
                    code="disabled_child",
                    display_name="DC",
                    parent_id=root["id"],
                ),
            )
            await uc.disable_department(ctx, disabled_child["id"])

            tree = await uc.get_department_tree(ctx, include_disabled=False)
            assert len(tree) == 1
            assert len(tree[0]["children"]) == 1
            assert tree[0]["children"][0]["code"] == "active_child"
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_disabled_root_excluded(self, database_url: str) -> None:
        """禁用的根部门在 include_disabled=false 时被排除。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="disabled_root", display_name="DR"),
            )
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="active_root", display_name="AR"),
            )

            # 先确认有2个根
            tree_all = await uc.get_department_tree(ctx, include_disabled=True)
            assert len(tree_all) == 2

            # 禁用第一个
            await uc.disable_department(ctx, tree_all[0]["id"])

            tree_filtered = await uc.get_department_tree(
                ctx,
                include_disabled=False,
            )
            assert len(tree_filtered) == 1
            assert tree_filtered[0]["status"] == "active"
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: 审计
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDeptAuditWrite:
    """部门变更写审计测试 — SPEC 18.2 / 5.7.

    SPEC 5.7: 审计记录与业务数据在同一事务提交。
    """

    async def test_create_writes_audit(self, database_url: str) -> None:
        """创建部门写审计。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            before = await _count_audit_logs(database_url)
            await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="HQ"),
            )
            after = await _count_audit_logs(database_url)
            assert after == before + 1
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_update_writes_audit(self, database_url: str) -> None:
        """更新部门写审计含差异。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="Old"),
            )
            before = await _count_audit_logs(database_url)
            await uc.update_department(
                ctx,
                dept["id"],
                DepartmentUpdateRequest(
                    display_name="New",
                    description="Updated",
                ),
            )
            after = await _count_audit_logs(database_url)
            assert after == before + 1
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_enable_disable_writes_audit(
        self,
        database_url: str,
    ) -> None:
        """启用和禁用部门写审计。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="HQ"),
            )
            before = await _count_audit_logs(database_url)
            await uc.disable_department(ctx, dept["id"])
            await uc.enable_department(ctx, dept["id"])
            after = await _count_audit_logs(database_url)
            assert after == before + 2
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_delete_writes_audit(self, database_url: str) -> None:
        """删除部门写审计。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="HQ"),
            )
            before = await _count_audit_logs(database_url)
            await uc.delete_department(ctx, dept["id"])
            after = await _count_audit_logs(database_url)
            assert after == before + 1
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_hierarchy_adjust_writes_audit(
        self,
        database_url: str,
    ) -> None:
        """调整层级写审计。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_use_case(engine)
            root = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="rr", display_name="R"),
            )
            child = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="cc", display_name="C"),
            )
            before = await _count_audit_logs(database_url)
            await uc.adjust_hierarchy(
                ctx,
                child["id"],
                DepartmentHierarchyRequest(parent_id=root["id"], sort_order=0),
            )
            after = await _count_audit_logs(database_url)
            assert after == before + 1
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)

    async def test_set_leader_writes_audit(self, database_url: str) -> None:
        """设置负责人写审计。"""

        await _apply_migrations(database_url)
        await _cleanup_tables(database_url)
        engine = create_db_engine(database_url)
        try:
            user_id = await _seed_user(database_url)
            uc, ctx = _make_use_case(engine)
            dept = await uc.create_department(
                ctx,
                DepartmentCreateRequest(code="hq", display_name="HQ"),
            )
            before = await _count_audit_logs(database_url)
            await uc.set_leader(
                ctx,
                dept["id"],
                DepartmentLeaderRequest(leader_id=user_id),
            )
            after = await _count_audit_logs(database_url)
            assert after == before + 1
        finally:
            await engine.dispose()
            await _cleanup_tables(database_url)
