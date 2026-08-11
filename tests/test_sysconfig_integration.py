"""系统配置模块集成测试 — SPEC 16.1 / 16.2 / 23.2.

覆盖:
  - 配置项 CRUD（创建/查询/更新/启用禁用/分组管理）。
  - 配置键在分组内唯一。
  - 配置值类型校验。
  - 敏感配置加密存储且 API 响应掩码。
  - 核心安全配置保护（测试证明）。
  - 配置变更写审计且与业务同事务。
  - 统一读取服务越键读取拒绝（测试证明）。
  - 密钥轮换 re-encrypt。

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
from app.modules.sysconfig.crypto import ConfigEncryptionService
from app.modules.sysconfig.errors import (
    ConfigAlreadyActiveError,
    ConfigAlreadyDisabledError,
    ConfigDuplicateKeyError,
    ConfigKeyNotDeclaredError,
    ConfigNotFoundError,
    ConfigValueTypeMismatchError,
    CoreSecurityConfigProtectedError,
)
from app.modules.sysconfig.schemas import (
    ConfigCreateRequest,
    ConfigUpdateRequest,
)
from app.modules.sysconfig.use_case import ConfigReadService, ConfigUseCase

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
    """清理系统配置与审计表。"""

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM sysconfig_items"))
            await conn.execute(text("DELETE FROM audit_logs"))
    finally:
        await engine.dispose()


# ── 测试 fixture ───────────────────────────────────────────────────────────

_TEST_ACTOR_ID = str(uuid4())
_TEST_KEY = ConfigEncryptionService.generate_key()


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


def _make_use_case(engine: AsyncEngine) -> ConfigUseCase:
    """构造 ConfigUseCase。"""

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    encryption = ConfigEncryptionService(_TEST_KEY)

    return ConfigUseCase(
        uow_factory=uow_factory,
        clock=SystemClock(),
        id_generator=UuidGenerator(),
        audit_factory=lambda session: SqlAlchemyAuditRepository(session),
        encryption_service=encryption,
    )


def _make_read_service(
    engine: AsyncEngine,
    declared_keys: frozenset[tuple[str, str]],
) -> ConfigReadService:
    """构造 ConfigReadService。"""

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    encryption = ConfigEncryptionService(_TEST_KEY)
    return ConfigReadService(
        uow_factory=uow_factory,
        encryption_service=encryption,
        declared_keys=declared_keys,
    )


def _ctx() -> UseCaseContext:
    """构造测试上下文。"""

    return UseCaseContext(
        request_id="test-sysconfig-req",
        actor_id=_TEST_ACTOR_ID,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 配置项 CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestConfigCRUDIntegration:
    """配置项 CRUD 集成测试 — SPEC 16.1."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> ConfigUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_create_config(self, use_case: ConfigUseCase) -> None:
        """创建配置项返回正确数据."""

        ctx = _ctx()
        request = ConfigCreateRequest(
            group="app",
            key="site_name",
            value_type="string",
            value="Apex Admin",
            description="站点名称",
        )
        result = asyncio.run(use_case.create_config(ctx, request))
        assert result["group"] == "app"
        assert result["key"] == "site_name"
        assert result["value_type"] == "string"
        assert result["value"] == "Apex Admin"
        assert result["status"] == "active"
        assert result["is_sensitive"] is False
        assert result["is_core_security"] is False

    def test_create_all_value_types(self, use_case: ConfigUseCase) -> None:
        """string/int/bool/json 四种类型均可创建."""

        ctx = _ctx()
        for vt, val in [
            ("string", "hello"),
            ("int", "42"),
            ("bool", "true"),
            ("json", '{"a": 1}'),
        ]:
            result = asyncio.run(
                use_case.create_config(
                    ctx,
                    ConfigCreateRequest(
                        group="types",
                        key=f"key_{vt}",
                        value_type=vt,
                        value=val,
                    ),
                ),
            )
            assert result["value_type"] == vt

    def test_create_duplicate_key_raises(
        self,
        use_case: ConfigUseCase,
    ) -> None:
        """配置键在分组内唯一 — SPEC 16.1."""

        ctx = _ctx()
        req = ConfigCreateRequest(
            group="dup",
            key="same_key",
            value_type="string",
            value="first",
        )
        asyncio.run(use_case.create_config(ctx, req))
        with pytest.raises(ConfigDuplicateKeyError):
            asyncio.run(use_case.create_config(ctx, req))

    def test_same_key_different_group_ok(
        self,
        use_case: ConfigUseCase,
    ) -> None:
        """不同分组允许相同键 — SPEC 16.1: 分组内唯一."""

        ctx = _ctx()
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="g1",
                    key="shared_key",
                    value_type="string",
                    value="val1",
                ),
            ),
        )
        result = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="g2",
                    key="shared_key",
                    value_type="string",
                    value="val2",
                ),
            ),
        )
        assert result["group"] == "g2"

    def test_get_config(self, use_case: ConfigUseCase) -> None:
        """查询配置项详情."""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="app",
                    key="port",
                    value_type="int",
                    value="8080",
                ),
            ),
        )
        detail = asyncio.run(use_case.get_config(ctx, created["id"]))
        assert detail["key"] == "port"
        assert detail["value"] == "8080"

    def test_get_config_not_found(self, use_case: ConfigUseCase) -> None:
        """查询不存在的配置项返回 404."""

        ctx = _ctx()
        with pytest.raises(ConfigNotFoundError):
            asyncio.run(use_case.get_config(ctx, uuid4()))

    def test_update_config(self, use_case: ConfigUseCase) -> None:
        """更新配置项值和说明."""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="app",
                    key="timeout",
                    value_type="int",
                    value="30",
                ),
            ),
        )
        result = asyncio.run(
            use_case.update_config(
                ctx,
                created["id"],
                ConfigUpdateRequest(value="60", description="更新超时"),
            ),
        )
        assert result["value"] == "60"
        assert result["description"] == "更新超时"

    def test_update_config_type_mismatch(
        self,
        use_case: ConfigUseCase,
    ) -> None:
        """更新配置值与声明类型不匹配返回参数错误 — SPEC 16.1."""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="app",
                    key="count",
                    value_type="int",
                    value="10",
                ),
            ),
        )
        with pytest.raises(ConfigValueTypeMismatchError):
            asyncio.run(
                use_case.update_config(
                    ctx,
                    created["id"],
                    ConfigUpdateRequest(value="not-an-int"),
                ),
            )

    def test_enable_disable_config(self, use_case: ConfigUseCase) -> None:
        """启用和禁用配置项."""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="app",
                    key="feature_x",
                    value_type="bool",
                    value="true",
                ),
            ),
        )

        disabled = asyncio.run(use_case.disable_config(ctx, created["id"]))
        assert disabled["status"] == "disabled"

        with pytest.raises(ConfigAlreadyDisabledError):
            asyncio.run(use_case.disable_config(ctx, created["id"]))

        enabled = asyncio.run(use_case.enable_config(ctx, created["id"]))
        assert enabled["status"] == "active"

        with pytest.raises(ConfigAlreadyActiveError):
            asyncio.run(use_case.enable_config(ctx, created["id"]))

    def test_list_configs_by_group(self, use_case: ConfigUseCase) -> None:
        """按分组查询配置项."""

        ctx = _ctx()
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="g_a",
                    key="k1",
                    value_type="string",
                    value="v1",
                ),
            ),
        )
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="g_b",
                    key="k2",
                    value_type="string",
                    value="v2",
                ),
            ),
        )
        result = asyncio.run(
            use_case.list_configs(ctx, group="g_a"),
        )
        assert len(result) == 1
        assert result[0]["group"] == "g_a"

    def test_list_groups(self, use_case: ConfigUseCase) -> None:
        """查询配置分组列表."""

        ctx = _ctx()
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="group1",
                    key="k1",
                    value_type="string",
                    value="v1",
                ),
            ),
        )
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="group2",
                    key="k2",
                    value_type="string",
                    value="v2",
                ),
            ),
        )
        groups = asyncio.run(use_case.list_groups(ctx))
        group_names = [g["group"] for g in groups]
        assert "group1" in group_names
        assert "group2" in group_names


# ═══════════════════════════════════════════════════════════════════════════════
# 敏感配置加密与掩码
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestSensitiveConfigEncryption:
    """敏感配置加密存储与不回显测试 — SPEC 16.1 / 23.2."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> ConfigUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_sensitive_value_encrypted_in_db(
        self,
        use_case: ConfigUseCase,
        migrated_database_url: str,
    ) -> None:
        """敏感配置值在数据库中加密存储 — SPEC 16.1 / 23.2."""

        ctx = _ctx()
        result = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="secrets",
                    key="db_password",
                    value_type="string",
                    value="super-secret-password",
                    is_sensitive=True,
                ),
            ),
        )

        # API 响应掩码
        from app.modules.sysconfig.schemas import SENSITIVE_MASK

        assert result["value"] == SENSITIVE_MASK
        assert result["is_sensitive"] is True

        # 数据库中存储的是密文而非明文
        async def _check_ciphertext() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.connect() as conn:
                    row = (
                        await conn.execute(
                            text(
                                "SELECT stored_value FROM sysconfig_items "
                                "WHERE id = :id",
                            ),
                            {"id": str(result["id"])},
                        )
                    ).fetchone()
                    assert row is not None
                    stored = row[0]
                    assert "super-secret-password" not in stored
                    assert stored != "super-secret-password"
            finally:
                await engine.dispose()

        asyncio.run(_check_ciphertext())

    def test_update_sensitive_config_masks(
        self,
        use_case: ConfigUseCase,
    ) -> None:
        """更新敏感配置后 API 响应仍掩码."""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="secrets",
                    key="api_key",
                    value_type="string",
                    value="old-api-key-value",
                    is_sensitive=True,
                ),
            ),
        )
        updated = asyncio.run(
            use_case.update_config(
                ctx,
                created["id"],
                ConfigUpdateRequest(value="new-api-key-value"),
            ),
        )
        from app.modules.sysconfig.schemas import SENSITIVE_MASK

        assert updated["value"] == SENSITIVE_MASK


# ═══════════════════════════════════════════════════════════════════════════════
# 核心安全配置保护（测试证明）
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestCoreSecurityProtection:
    """核心安全配置保护测试 — SPEC 16.1.

    SPEC 16.1: "核心安全配置不得由普通后台配置随意覆盖"。
    """

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> ConfigUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_core_security_cannot_be_updated(
        self,
        use_case: ConfigUseCase,
    ) -> None:
        """核心安全配置不可通过普通后台更新 — SPEC 16.1."""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="security",
                    key="password_policy_min_length",
                    value_type="int",
                    value="12",
                    is_core_security=True,
                ),
            ),
        )
        with pytest.raises(CoreSecurityConfigProtectedError):
            asyncio.run(
                use_case.update_config(
                    ctx,
                    created["id"],
                    ConfigUpdateRequest(value="4"),
                ),
            )

    def test_core_security_cannot_be_disabled(
        self,
        use_case: ConfigUseCase,
    ) -> None:
        """核心安全配置不可通过普通后台禁用."""

        ctx = _ctx()
        created = asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="security",
                    key="enforce_2fa",
                    value_type="bool",
                    value="true",
                    is_core_security=True,
                ),
            ),
        )
        with pytest.raises(CoreSecurityConfigProtectedError):
            asyncio.run(use_case.disable_config(ctx, created["id"]))

    def test_core_security_cannot_be_enabled(
        self,
        use_case: ConfigUseCase,
        migrated_database_url: str,
    ) -> None:
        """核心安全配置不可通过普通后台启用（即使已禁用）."""

        ctx = _ctx()
        config_id = uuid4()

        async def _seed_disabled_core_security() -> None:
            from app.infrastructure.db.engine import create_db_engine

            now = datetime.now(UTC)
            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            'INSERT INTO sysconfig_items (id, "group", key, '
                            "value_type, stored_value, is_sensitive, "
                            "is_core_security, description, status, "
                            "created_at, updated_at) "
                            "VALUES (:id, 'sec', 'test', 'string', 'val', "
                            "false, true, NULL, 'disabled', :t, :t)",
                        ),
                        {"id": str(config_id), "t": now},
                    )
            finally:
                await engine.dispose()

        asyncio.run(_seed_disabled_core_security())

        with pytest.raises(CoreSecurityConfigProtectedError):
            asyncio.run(use_case.enable_config(ctx, config_id))


# ═══════════════════════════════════════════════════════════════════════════════
# 审计与业务同事务
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestConfigAuditIntegration:
    """配置变更写审计且与业务同事务 — SPEC 16.1 / 5.7 / 18.2."""

    @pytest.fixture()
    def use_case(
        self,
        migrated_database_url: str,
    ) -> ConfigUseCase:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        self._engine = engine  # type: ignore[attr-defined]
        return _make_use_case(engine)

    def test_create_writes_audit(
        self,
        use_case: ConfigUseCase,
        migrated_database_url: str,
    ) -> None:
        """创建配置项写审计 — SPEC 18.2."""

        ctx = _ctx()
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="audit_test",
                    key="key1",
                    value_type="string",
                    value="val1",
                ),
            ),
        )

        async def _check_audit_count() -> None:
            from app.infrastructure.db.engine import create_db_engine

            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.connect() as conn:
                    count = (
                        await conn.execute(
                            text(
                                "SELECT COUNT(*) FROM audit_logs "
                                "WHERE module = 'sysconfig' "
                                "AND action = 'sysconfig.create'",
                            ),
                        )
                    ).scalar()
                    assert count == 1
            finally:
                await engine.dispose()

        asyncio.run(_check_audit_count())

    def test_audit_same_transaction_rollback(
        self,
        use_case: ConfigUseCase,
        migrated_database_url: str,
    ) -> None:
        """审计与业务同事务——业务回滚时审计也回滚 — SPEC 5.7."""

        ctx = _ctx()
        # 正常创建一个配置项
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="rollback_test",
                    key="k1",
                    value_type="string",
                    value="v1",
                ),
            ),
        )

        # 尝试创建一个会导致错误的配置（重复键），确认审计不残留
        with pytest.raises(ConfigDuplicateKeyError):
            asyncio.run(
                use_case.create_config(
                    ctx,
                    ConfigCreateRequest(
                        group="rollback_test",
                        key="k1",
                        value_type="string",
                        value="v2",
                    ),
                ),
            )

        from app.infrastructure.db.engine import create_db_engine

        async def _check_audit_after_rollback() -> None:
            engine = create_db_engine(migrated_database_url)
            try:
                async with engine.connect() as conn:
                    count = (
                        await conn.execute(
                            text(
                                "SELECT COUNT(*) FROM audit_logs "
                                "WHERE module = 'sysconfig' "
                                "AND action = 'sysconfig.create'",
                            ),
                        )
                    ).scalar()
                    assert count == 1
            finally:
                await engine.dispose()

        asyncio.run(_check_audit_after_rollback())


# ═══════════════════════════════════════════════════════════════════════════════
# 统一读取服务（越键读取拒绝 — 测试证明）
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestConfigReadServiceIntegration:
    """统一配置读取服务测试 — SPEC 16.2.

    SPEC 16.2: "业务模块只读取自己声明依赖的配置"。
    SPEC 16.2: "不提供可以在任意位置随意读取任意键值的隐式全局配置对象"。
    """

    @pytest.fixture()
    def setup(
        self,
        migrated_database_url: str,
    ) -> tuple[ConfigUseCase, ConfigReadService]:
        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        use_case = _make_use_case(engine)

        # 创建配置项
        ctx = _ctx()
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="app",
                    key="site_name",
                    value_type="string",
                    value="My App",
                ),
            ),
        )
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="app",
                    key="max_items",
                    value_type="int",
                    value="100",
                ),
            ),
        )
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="app",
                    key="secret_token",
                    value_type="string",
                    value="hidden-secret",
                    is_sensitive=True,
                ),
            ),
        )
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="other",
                    key="not_declared",
                    value_type="string",
                    value="should-not-be-accessible",
                ),
            ),
        )

        # 声明式白名单——只允许读取 app 分组的前三个键
        read_service = _make_read_service(
            engine,
            declared_keys=frozenset(
                {
                    ("app", "site_name"),
                    ("app", "max_items"),
                    ("app", "secret_token"),
                },
            ),
        )
        return use_case, read_service

    def test_read_declared_string(
        self,
        setup: tuple[ConfigUseCase, ConfigReadService],
    ) -> None:
        """读取声明的字符串配置."""

        _, read_service = setup
        result = asyncio.run(read_service.read("app", "site_name"))
        assert result == "My App"

    def test_read_declared_int(
        self,
        setup: tuple[ConfigUseCase, ConfigReadService],
    ) -> None:
        """读取声明的 int 配置——类型转换."""

        _, read_service = setup
        result = asyncio.run(read_service.read("app", "max_items"))
        assert result == 100
        assert isinstance(result, int)

    def test_read_declared_sensitive_decrypts(
        self,
        setup: tuple[ConfigUseCase, ConfigReadService],
    ) -> None:
        """读取声明的敏感配置——解密后返回明文 — SPEC 16.1 / 23.2."""

        _, read_service = setup
        result = asyncio.run(read_service.read("app", "secret_token"))
        assert result == "hidden-secret"

    def test_read_undeclared_key_raises(
        self,
        setup: tuple[ConfigUseCase, ConfigReadService],
    ) -> None:
        """越键读取报错——测试证明 — SPEC 16.2."""

        _, read_service = setup
        with pytest.raises(ConfigKeyNotDeclaredError):
            asyncio.run(read_service.read("other", "not_declared"))

    def test_read_nonexistent_declared_key_raises(
        self,
        setup: tuple[ConfigUseCase, ConfigReadService],
        migrated_database_url: str,
    ) -> None:
        """读取声明的键但配置不存在返回 404."""

        from app.infrastructure.db.engine import create_db_engine

        engine = create_db_engine(migrated_database_url)
        read_service = _make_read_service(
            engine,
            declared_keys=frozenset({("app", "nonexistent")}),
        )
        with pytest.raises(ConfigNotFoundError):
            asyncio.run(read_service.read("app", "nonexistent"))


# ═══════════════════════════════════════════════════════════════════════════════
# 密钥轮换 re-encrypt
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.integration
class TestReEncryptIntegration:
    """敏感配置密钥轮换 re-encrypt 集成测试 — SPEC 23.2."""

    def test_re_encrypt_rotates_all_sensitive_items(
        self,
        migrated_database_url: str,
    ) -> None:
        """re-encrypt 将全部敏感配置项用新密钥重加密 — SPEC 23.2."""

        from app.infrastructure.db.engine import create_db_engine

        old_key = ConfigEncryptionService.generate_key()
        new_key = ConfigEncryptionService.generate_key()

        engine = create_db_engine(migrated_database_url)
        old_encryption = ConfigEncryptionService(old_key)

        # 用旧密钥创建敏感配置
        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(engine)

        use_case = ConfigUseCase(
            uow_factory=uow_factory,
            clock=SystemClock(),
            id_generator=UuidGenerator(),
            audit_factory=lambda s: SqlAlchemyAuditRepository(s),
            encryption_service=old_encryption,
        )

        ctx = _ctx()
        asyncio.run(
            use_case.create_config(
                ctx,
                ConfigCreateRequest(
                    group="crypto",
                    key="secret1",
                    value_type="string",
                    value="plaintext-secret-1",
                    is_sensitive=True,
                ),
            ),
        )

        # 执行 re-encrypt（双密钥短期切换）
        rotation_service = ConfigEncryptionService(
            new_key,
            previous_key=old_key,
        )

        async def _do_re_encrypt() -> None:
            uow = SqlAlchemyUnitOfWork(engine)
            async with uow:
                from app.modules.sysconfig.adapter import (
                    SqlAlchemyConfigRepository,
                )
                from app.modules.sysconfig.models import ConfigItem

                repo = SqlAlchemyConfigRepository(uow.session)
                items = await repo.list_sensitive_items()
                for item in items:
                    new_cipher = rotation_service.rotate(item.stored_value)
                    updated = ConfigItem(
                        id=item.id,
                        group=item.group,
                        key=item.key,
                        value_type=item.value_type,
                        stored_value=new_cipher,
                        is_sensitive=item.is_sensitive,
                        is_core_security=item.is_core_security,
                        description=item.description,
                        status=item.status,
                        created_at=item.created_at,
                        updated_at=datetime.now(UTC),
                        created_by=item.created_by,
                        updated_by="test:re-encrypt",
                    )
                    await repo.save(updated)
                await uow.commit()

        asyncio.run(_do_re_encrypt())

        # 验证：仅持有新密钥的服务可以解密
        new_encryption = ConfigEncryptionService(new_key)
        new_read_service = ConfigReadService(
            uow_factory=uow_factory,
            encryption_service=new_encryption,
            declared_keys=frozenset({("crypto", "secret1")}),
        )
        result = asyncio.run(new_read_service.read("crypto", "secret1"))
        assert result == "plaintext-secret-1"

        # 旧密钥无法解密重加密后的密文
        old_read_service = ConfigReadService(
            uow_factory=uow_factory,
            encryption_service=old_encryption,
            declared_keys=frozenset({("crypto", "secret1")}),
        )
        from app.modules.sysconfig.crypto import ConfigEncryptionError

        with pytest.raises(ConfigEncryptionError):
            asyncio.run(old_read_service.read("crypto", "secret1"))
