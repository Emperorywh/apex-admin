"""数据字典模块集成测试 — SPEC 17.1 / 17.2 / 5.7 / 18.2.

覆盖:
  - 字典类型与字典项 CRUD（创建/查询/更新/启用禁用/删除）。
  - 字典编码稳定唯一，冲突返回稳定冲突错误码。
  - 字典项含显示文本/稳定值/排序/扩展元数据。
  - 引用登记 Port：登记/释放/计数；被引用的字典类型删除被拒绝。
  - 字典项变更写审计且与业务同事务。

连接真实 PostgreSQL（SPEC 28.2）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.context import UseCaseContext
from app.application.ports import SystemClock, UuidGenerator
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.audit.adapter import SqlAlchemyAuditRepository
from app.modules.dict.adapter import (
    SqlAlchemyReferenceRegistry,
)
from app.modules.dict.errors import (
    DictItemAlreadyActiveError,
    DictItemAlreadyDisabledError,
    DictItemDuplicateValueError,
    DictItemNotFoundError,
    DictTypeAlreadyActiveError,
    DictTypeAlreadyDisabledError,
    DictTypeDuplicateCodeError,
    DictTypeNotFoundError,
    DictTypeReferencedError,
)
from app.modules.dict.schemas import (
    DictItemCreateRequest,
    DictItemUpdateRequest,
    DictTypeCreateRequest,
    DictTypeUpdateRequest,
)
from app.modules.dict.use_case import DictUseCase

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine


# ── 迁移与清理 ─────────────────────────────────────────────────────────────


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
    """清理字典与审计表。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM dict_references"))
            await conn.execute(text("DELETE FROM dict_items"))
            await conn.execute(text("DELETE FROM dict_types"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


# ── 测试 fixture ───────────────────────────────────────────────────────────

_TEST_ACTOR_ID = str(uuid4())


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    """对测试数据库执行迁移。"""

    asyncio.run(_apply_migrations(database_url))
    yield database_url


@pytest.fixture(autouse=True)
def _clean_tables(migrated_database_url: str) -> Iterator[None]:
    """每个测试前后清理全部表。"""

    asyncio.run(_cleanup_tables(migrated_database_url))
    yield
    asyncio.run(_cleanup_tables(migrated_database_url))


def _make_use_case(engine: AsyncEngine) -> DictUseCase:
    """构造 DictUseCase。"""

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    return DictUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=lambda session: SqlAlchemyAuditRepository(session),
    )


def _ctx() -> UseCaseContext:
    """构造测试上下文。"""

    return UseCaseContext(
        request_id="test-dict-req",
        actor_id=_TEST_ACTOR_ID,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 字典类型 CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDictTypeCRUDIntegration:
    """字典类型 CRUD 集成测试 — SPEC 17.1."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> DictUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_create_dict_type(self, use_case: DictUseCase) -> None:
        """创建字典类型返回正确数据。"""

        ctx = _ctx()
        result = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(
                    code="gender",
                    name="性别",
                    description="用户性别",
                ),
            ),
        )
        assert result["code"] == "gender"
        assert result["name"] == "性别"
        assert result["status"] == "active"

    def test_create_duplicate_code_raises(self, use_case: DictUseCase) -> None:
        """字典编码唯一冲突返回稳定冲突错误码 — SPEC 17.1."""

        ctx = _ctx()
        req = DictTypeCreateRequest(code="status", name="状态")
        asyncio.run(use_case.create_dict_type(ctx, req))
        with pytest.raises(DictTypeDuplicateCodeError) as exc_info:
            asyncio.run(use_case.create_dict_type(ctx, req))
        # SPEC: 稳定冲突错误码
        assert exc_info.value.code == "DICT.TYPE_DUPLICATE_CODE"

    def test_get_dict_type(self, use_case: DictUseCase) -> None:
        """查询字典类型详情。"""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="priority", name="优先级"),
            ),
        )
        detail = asyncio.run(use_case.get_dict_type(ctx, created["id"]))
        assert detail["code"] == "priority"

    def test_get_dict_type_not_found(self, use_case: DictUseCase) -> None:
        """查询不存在的字典类型返回 404。"""

        ctx = _ctx()
        with pytest.raises(DictTypeNotFoundError):
            asyncio.run(use_case.get_dict_type(ctx, uuid4()))

    def test_list_dict_types(self, use_case: DictUseCase) -> None:
        """查询字典类型列表。"""

        ctx = _ctx()
        asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="type_a", name="A"),
            ),
        )
        asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="type_b", name="B"),
            ),
        )
        results, total = asyncio.run(use_case.list_dict_types(ctx))
        assert total == 2
        codes = [r["code"] for r in results]
        assert "type_a" in codes
        assert "type_b" in codes

    def test_update_dict_type(self, use_case: DictUseCase) -> None:
        """更新字典类型名称和描述。"""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="upd", name="旧名"),
            ),
        )
        result = asyncio.run(
            use_case.update_dict_type(
                ctx,
                created["id"],
                DictTypeUpdateRequest(name="新名", description="更新后"),
            ),
        )
        assert result["name"] == "新名"
        assert result["description"] == "更新后"
        # 编码不可变更
        assert result["code"] == "upd"

    def test_enable_disable_dict_type(self, use_case: DictUseCase) -> None:
        """启用和禁用字典类型。"""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="toggle", name="开关"),
            ),
        )

        disabled = asyncio.run(use_case.disable_dict_type(ctx, created["id"]))
        assert disabled["status"] == "disabled"

        with pytest.raises(DictTypeAlreadyDisabledError):
            asyncio.run(use_case.disable_dict_type(ctx, created["id"]))

        enabled = asyncio.run(use_case.enable_dict_type(ctx, created["id"]))
        assert enabled["status"] == "active"

        with pytest.raises(DictTypeAlreadyActiveError):
            asyncio.run(use_case.enable_dict_type(ctx, created["id"]))


# ═══════════════════════════════════════════════════════════════════════════════
# 字典项 CRUD — 含显示文本/稳定值/排序/扩展元数据
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDictItemCRUDIntegration:
    """字典项 CRUD 集成测试 — SPEC 17.2."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> DictUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_create_dict_item_with_all_fields(self, use_case: DictUseCase) -> None:
        """字典项含显示文本/稳定值/排序/扩展元数据 — SPEC 17.2."""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="level", name="级别"),
            ),
        )
        result = asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(
                    label="高",
                    value="high",
                    sort_order=1,
                    metadata={"color": "red", "weight": 10},
                    description="高优先级",
                ),
            ),
        )
        assert result["label"] == "高"
        assert result["value"] == "high"
        assert result["sort_order"] == 1
        assert result["metadata"] == {"color": "red", "weight": 10}
        assert result["description"] == "高优先级"
        assert result["status"] == "active"

    def test_create_dict_item_duplicate_value(self, use_case: DictUseCase) -> None:
        """字典项稳定值在同类内唯一。"""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="dup_val", name="测试"),
            ),
        )
        asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="A", value="same"),
            ),
        )
        with pytest.raises(DictItemDuplicateValueError):
            asyncio.run(
                use_case.create_dict_item(
                    ctx,
                    dt["id"],
                    DictItemCreateRequest(label="B", value="same"),
                ),
            )

    def test_list_dict_items_ordered(self, use_case: DictUseCase) -> None:
        """字典项列表按 sort_order 升序排列。"""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="ordered", name="有序"),
            ),
        )
        asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="C", value="c", sort_order=2),
            ),
        )
        asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="A", value="a", sort_order=0),
            ),
        )
        asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="B", value="b", sort_order=1),
            ),
        )
        results = asyncio.run(use_case.list_dict_items(ctx, dt["id"]))
        assert [r["sort_order"] for r in results] == [0, 1, 2]

    def test_update_dict_item(self, use_case: DictUseCase) -> None:
        """更新字典项的显示文本/稳定值/排序/扩展元数据。"""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="upd_item", name="更新项"),
            ),
        )
        created = asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="旧", value="old", sort_order=0),
            ),
        )
        result = asyncio.run(
            use_case.update_dict_item(
                ctx,
                created["id"],
                DictItemUpdateRequest(
                    label="新",
                    value="new",
                    sort_order=5,
                    metadata={"updated": True},
                ),
            ),
        )
        assert result["label"] == "新"
        assert result["value"] == "new"
        assert result["sort_order"] == 5
        assert result["metadata"] == {"updated": True}

    def test_enable_disable_dict_item(self, use_case: DictUseCase) -> None:
        """启用和禁用字典项。"""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="toggle_item", name="开关项"),
            ),
        )
        created = asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="X", value="x"),
            ),
        )

        disabled = asyncio.run(use_case.disable_dict_item(ctx, created["id"]))
        assert disabled["status"] == "disabled"

        with pytest.raises(DictItemAlreadyDisabledError):
            asyncio.run(use_case.disable_dict_item(ctx, created["id"]))

        enabled = asyncio.run(use_case.enable_dict_item(ctx, created["id"]))
        assert enabled["status"] == "active"

        with pytest.raises(DictItemAlreadyActiveError):
            asyncio.run(use_case.enable_dict_item(ctx, created["id"]))

    def test_delete_dict_item(self, use_case: DictUseCase) -> None:
        """删除字典项。"""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="del_item", name="删除项"),
            ),
        )
        created = asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="Y", value="y"),
            ),
        )
        asyncio.run(use_case.delete_dict_item(ctx, created["id"]))
        with pytest.raises(DictItemNotFoundError):
            asyncio.run(use_case.get_dict_item(ctx, created["id"]))


# ═══════════════════════════════════════════════════════════════════════════════
# 引用登记与删除保护 — SPEC 17.1
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReferenceRegistryAndDeleteProtection:
    """引用登记 Port 与删除保护测试 — SPEC 17.1."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> DictUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_referenced_dict_type_delete_rejected(
        self,
        use_case: DictUseCase,
        migrated_database_url: str,
    ) -> None:
        """经引用登记 Port 登记为被引用的字典类型删除被拒绝 — SPEC 17.1."""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="protected", name="受保护"),
            ),
        )

        # 登记引用
        async def _register_ref() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO dict_references "
                            "(id, dict_type_code, module_code, resource_id, "
                            "created_at) VALUES (:id, 'protected', 'identity', "
                            "'user-123', :t)",
                        ),
                        {"id": str(uuid4()), "t": datetime.now(UTC)},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_register_ref())

        # 删除被拒绝
        with pytest.raises(DictTypeReferencedError) as exc_info:
            asyncio.run(use_case.delete_dict_type(ctx, dt["id"]))
        assert exc_info.value.code == "DICT.TYPE_REFERENCED"

    def test_unreferenced_dict_type_delete_ok(self, use_case: DictUseCase) -> None:
        """未被引用的字典类型可正常删除。"""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="free", name="可删除"),
            ),
        )
        asyncio.run(use_case.delete_dict_type(ctx, dt["id"]))
        with pytest.raises(DictTypeNotFoundError):
            asyncio.run(use_case.get_dict_type(ctx, dt["id"]))

    def test_register_reference_idempotent(
        self,
        migrated_database_url: str,
    ) -> None:
        """引用登记幂等——重复登记不产生重复记录。"""

        async def _test() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                registry = SqlAlchemyReferenceRegistry(
                    (await SqlAlchemyUnitOfWork(engine).__aenter__()).session,
                )
                # 这个测试直接使用 UoW
                uow = SqlAlchemyUnitOfWork(engine)
                async with uow:
                    registry = SqlAlchemyReferenceRegistry(uow.session)
                    await registry.register_reference(
                        dict_type_code="idem",
                        module_code="identity",
                        resource_id="res-1",
                        created_at=datetime.now(UTC),
                    )
                    await registry.register_reference(
                        dict_type_code="idem",
                        module_code="identity",
                        resource_id="res-1",
                        created_at=datetime.now(UTC),
                    )
                    count = await registry.count_references("idem")
                    assert count == 1
                    await uow.commit()
            finally:
                await engine.dispose()

        asyncio.run(_test())

    def test_release_reference(self, migrated_database_url: str) -> None:
        """释放引用后计数归零。"""

        async def _test() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                uow = SqlAlchemyUnitOfWork(engine)
                async with uow:
                    registry = SqlAlchemyReferenceRegistry(uow.session)
                    await registry.register_reference(
                        dict_type_code="release",
                        module_code="org",
                        resource_id="dept-1",
                        created_at=datetime.now(UTC),
                    )
                    assert await registry.count_references("release") == 1
                    await registry.release_reference(
                        "release",
                        "org",
                        "dept-1",
                    )
                    assert await registry.count_references("release") == 0
                    await uow.commit()
            finally:
                await engine.dispose()

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════════════
# 字典项变更写审计
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestDictAuditIntegration:
    """字典项变更写审计且与业务同事务 — SPEC 17.2 / 5.7 / 18.2."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> DictUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_create_item_writes_audit(
        self,
        use_case: DictUseCase,
        migrated_database_url: str,
    ) -> None:
        """创建字典项写审计 — SPEC 18.2."""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="audit_create", name="审计创建"),
            ),
        )
        asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="测试", value="test"),
            ),
        )

        async def _check() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.connect() as conn:
                    count = (
                        await conn.execute(
                            text(
                                "SELECT COUNT(*) FROM audit_logs "
                                "WHERE module = 'dict' "
                                "AND action = 'dict.item.create'",
                            ),
                        )
                    ).scalar()
                    assert count == 1
            finally:
                await engine.dispose()

        asyncio.run(_check())

    def test_update_item_writes_audit(
        self,
        use_case: DictUseCase,
        migrated_database_url: str,
    ) -> None:
        """更新字典项写审计。"""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="audit_upd", name="审计更新"),
            ),
        )
        created = asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="旧", value="old"),
            ),
        )
        asyncio.run(
            use_case.update_dict_item(
                ctx,
                created["id"],
                DictItemUpdateRequest(label="新", value="new", sort_order=0),
            ),
        )

        async def _check() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.connect() as conn:
                    count = (
                        await conn.execute(
                            text(
                                "SELECT COUNT(*) FROM audit_logs "
                                "WHERE module = 'dict' "
                                "AND action = 'dict.item.update'",
                            ),
                        )
                    ).scalar()
                    assert count == 1
            finally:
                await engine.dispose()

        asyncio.run(_check())

    def test_audit_same_transaction_rollback(
        self,
        use_case: DictUseCase,
        migrated_database_url: str,
    ) -> None:
        """审计与业务同事务——业务回滚时审计也回滚 — SPEC 5.7."""

        ctx = _ctx()
        dt = asyncio.run(
            use_case.create_dict_type(
                ctx,
                DictTypeCreateRequest(code="audit_rb", name="审计回滚"),
            ),
        )
        # 正常创建一个字典项
        asyncio.run(
            use_case.create_dict_item(
                ctx,
                dt["id"],
                DictItemCreateRequest(label="成功", value="ok"),
            ),
        )
        # 尝试创建一个会导致错误的字典项（重复值），确认审计不残留
        with pytest.raises(DictItemDuplicateValueError):
            asyncio.run(
                use_case.create_dict_item(
                    ctx,
                    dt["id"],
                    DictItemCreateRequest(label="重复", value="ok"),
                ),
            )

        async def _check() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.connect() as conn:
                    count = (
                        await conn.execute(
                            text(
                                "SELECT COUNT(*) FROM audit_logs "
                                "WHERE module = 'dict' "
                                "AND action = 'dict.item.create'",
                            ),
                        )
                    ).scalar()
                    assert count == 1
            finally:
                await engine.dispose()

        asyncio.run(_check())
