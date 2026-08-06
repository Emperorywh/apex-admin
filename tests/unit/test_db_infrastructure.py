"""数据库基础设施单元测试（SPEC §5.6、§8.1）。

覆盖不需要真实数据库的验收条件：
- 异常映射函数：IntegrityError → IntegrityConstraintError，OperationalError → DatabaseOperationError
- 引擎工厂：URL 协议校验和参数传递
- UoW 生命周期边界：未激活时 session/commit/rollback 抛出 RuntimeError
- DbPoolProvider 状态边界：未初始化时操作抛出异常
- UnitOfWork Port：是抽象类
- Settings：pool_size 和 max_overflow 配置校验
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config.settings import AppEnv, Settings
from app.errors import AppError, DatabaseOperationError, IntegrityConstraintError
from app.health.providers import DbPoolProvider
from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider
from app.infrastructure.database.engine import create_engine
from app.infrastructure.database.exceptions import translate_db_exception
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.ports.unit_of_work import UnitOfWork

pytestmark = [pytest.mark.unit, pytest.mark.g1]

# 测试用有效密钥
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"

_CONFIG_ENV_VARS = (
    "APP_ENV",
    "DATABASE_URL",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "ACCESS_TOKEN_HMAC_KEY",
    "REFRESH_TOKEN_HMAC_KEY",
    "CONFIG_ENCRYPTION_KEY",
    "FILE_STORAGE_ROOT",
    "ALLOWED_ORIGINS",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除配置相关环境变量，确保测试不受外部环境影响。"""
    for var in _CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _make_settings(**overrides: Any) -> Settings:
    """构造测试用 Settings。"""
    defaults: dict[str, Any] = {
        "_env_file": None,
        "app_env": AppEnv.TESTING,
        "database_url": "postgresql+psycopg://apex:secret@localhost:5432/apex_admin_test",
        "access_token_hmac_key": _VALID_ACCESS_KEY,
        "refresh_token_hmac_key": _VALID_REFRESH_KEY,
        "config_encryption_key": _VALID_ENCRYPTION_KEY,
        "file_storage_root": "/tmp/apex-test-files",
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# 异常映射（验收条件：数据库异常映射为稳定应用异常）
# ---------------------------------------------------------------------------


class TestTranslateDbException:
    """验证 translate_db_exception 正确映射 SQLAlchemy 异常。"""

    def test_integrity_error_maps_to_app_error(self) -> None:
        """IntegrityError 映射为 IntegrityConstraintError。"""
        original = IntegrityError(
            "INSERT INTO test_uow_items VALUES (...)",
            {"name": "dup"},
            Exception("unique constraint violation"),
        )
        result = translate_db_exception(original)
        assert isinstance(result, IntegrityConstraintError)
        assert result is not original

    def test_operational_error_maps_to_app_error(self) -> None:
        """OperationalError 映射为 DatabaseOperationError。"""
        original = OperationalError(
            "SELECT 1",
            {},
            Exception("connection refused"),
        )
        result = translate_db_exception(original)
        assert isinstance(result, DatabaseOperationError)
        assert result is not original

    def test_non_db_exception_unchanged(self) -> None:
        """非数据库异常原样返回。"""
        original = ValueError("not a db error")
        result = translate_db_exception(original)
        assert result is original

    def test_mapped_error_has_stable_code(self) -> None:
        """映射后的应用异常携带稳定错误码。"""
        integrity = IntegrityError("stmt", {}, Exception("dup"))
        operational = OperationalError("stmt", {}, Exception("conn"))

        assert translate_db_exception(integrity).code == "DB.INTEGRITY_CONSTRAINT"
        assert translate_db_exception(operational).code == "DB.OPERATION_ERROR"

    def test_mapped_error_is_app_error_subclass(self) -> None:
        """映射后的异常是 AppError 子类。"""
        integrity = IntegrityError("stmt", {}, Exception("dup"))
        result = translate_db_exception(integrity)
        assert isinstance(result, AppError)

    def test_already_mapped_error_not_re_mapped(self) -> None:
        """已经是应用异常的不再被二次映射。"""
        app_error = IntegrityConstraintError("already mapped")
        result = translate_db_exception(app_error)
        assert result is app_error


# ---------------------------------------------------------------------------
# 引擎工厂（验收条件：使用 create_async_engine 和 postgresql+psycopg URL）
# ---------------------------------------------------------------------------


class TestEngineFactory:
    """验证引擎工厂的 URL 校验和参数传递。"""

    def test_rejects_non_psycopg_url(self) -> None:
        """非 postgresql+psycopg URL 被拒绝。"""
        with pytest.raises(ValueError, match="postgresql\\+psycopg"):
            create_engine("postgresql+asyncpg://user:pass@host/db")

    def test_rejects_sqlite_url(self) -> None:
        """SQLite URL 被拒绝（SPEC §5.4：禁止使用 SQLite）。"""
        with pytest.raises(ValueError, match="postgresql\\+psycopg"):
            create_engine("sqlite:///test.db")

    def test_accepts_valid_psycopg_url(self) -> None:
        """postgresql+psycopg URL 创建引擎成功。"""
        engine = create_engine(
            "postgresql+psycopg://apex:secret@localhost:5432/apex_admin",
            pool_size=3,
            max_overflow=2,
        )
        assert engine is not None
        # 引擎 URL 确认使用了 psycopg 驱动
        assert "psycopg" in str(engine.url)
        engine.sync_engine.dispose()

    def test_default_pool_params(self) -> None:
        """默认连接池参数为 pool_size=5、max_overflow=5。"""
        engine = create_engine("postgresql+psycopg://apex:secret@localhost:5432/apex_admin")
        pool = engine.pool
        # QueuePool 的 _pool.maxsize = pool_size + max_overflow
        assert pool.size() == 5
        assert getattr(pool, "_max_overflow", 5) == 5
        engine.sync_engine.dispose()


# ---------------------------------------------------------------------------
# UoW 生命周期边界（验收条件：禁止在 UoW 生命周期之外复用数据库会话）
# ---------------------------------------------------------------------------


class TestUnitOfWorkLifecycleBoundary:
    """验证 UoW 在未激活状态下拒绝操作。"""

    def test_session_raises_before_enter(self) -> None:
        """未进入上下文时访问 session 抛出 RuntimeError。"""
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("postgresql+psycopg://u:p@h:5432/db")
        uow = SqlAlchemyUnitOfWork(engine)
        with pytest.raises(RuntimeError, match="未激活"):
            _ = uow.session
        engine.sync_engine.dispose()

    def test_commit_raises_before_enter(self) -> None:
        """未进入上下文时调用 commit 抛出 RuntimeError。"""
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("postgresql+psycopg://u:p@h:5432/db")
        uow = SqlAlchemyUnitOfWork(engine)

        async def _try_commit() -> None:
            await uow.commit()

        import asyncio

        with pytest.raises(RuntimeError, match="未激活"):
            asyncio.run(_try_commit())
        engine.sync_engine.dispose()

    def test_rollback_raises_before_enter(self) -> None:
        """未进入上下文时调用 rollback 抛出 RuntimeError。"""
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("postgresql+psycopg://u:p@h:5432/db")
        uow = SqlAlchemyUnitOfWork(engine)

        async def _try_rollback() -> None:
            await uow.rollback()

        import asyncio

        with pytest.raises(RuntimeError, match="未激活"):
            asyncio.run(_try_rollback())
        engine.sync_engine.dispose()


# ---------------------------------------------------------------------------
# DbPoolProvider 状态边界
# ---------------------------------------------------------------------------


class TestDbPoolProviderBoundary:
    """验证 DbPoolProvider 在未初始化状态下拒绝操作。"""

    async def test_check_connection_false_before_init(self) -> None:
        """未初始化时连通性检查返回 False。"""
        settings = _make_settings()
        provider = SqlAlchemyDbPoolProvider(settings)
        assert await provider.check_connection() is False

    async def test_create_uow_raises_before_init(self) -> None:
        """未初始化时创建 UoW 抛出 RuntimeError。"""
        settings = _make_settings()
        provider = SqlAlchemyDbPoolProvider(settings)
        with pytest.raises(RuntimeError, match="未初始化"):
            provider.create_unit_of_work()

    async def test_provider_is_db_pool_provider(self) -> None:
        """SqlAlchemyDbPoolProvider 是 DbPoolProvider 的子类。"""
        settings = _make_settings()
        provider = SqlAlchemyDbPoolProvider(settings)
        assert isinstance(provider, DbPoolProvider)


# ---------------------------------------------------------------------------
# UnitOfWork Port（验收条件：UnitOfWork 定义为抽象 Port）
# ---------------------------------------------------------------------------


class TestUnitOfWorkPort:
    """验证 UnitOfWork 是抽象端口。"""

    def test_cannot_instantiate_abstract(self) -> None:
        """UnitOfWork 是抽象类，不能直接实例化。"""
        with pytest.raises(TypeError):
            UnitOfWork()  # type: ignore[abstract]

    def test_sqlalchemy_uow_is_uow(self) -> None:
        """SqlAlchemyUnitOfWork 是 UnitOfWork 的子类。"""
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine("postgresql+psycopg://u:p@h:5432/db")
        uow = SqlAlchemyUnitOfWork(engine)
        assert isinstance(uow, UnitOfWork)
        engine.sync_engine.dispose()

    def test_has_required_abstract_methods(self) -> None:
        """UnitOfWork 定义了全部必需的抽象方法。"""
        abstract_methods = {
            "__aenter__",
            "__aexit__",
            "commit",
            "rollback",
        }
        assert abstract_methods.issubset(UnitOfWork.__abstractmethods__)


# ---------------------------------------------------------------------------
# Settings 连接池配置（验收条件：可配置 pool_size 和 max_overflow）
# ---------------------------------------------------------------------------


class TestSettingsPoolConfig:
    """验证 Settings 中 pool_size 和 max_overflow 配置。"""

    def test_defaults(self) -> None:
        """默认 pool_size=5、max_overflow=5。"""
        settings = _make_settings()
        assert settings.db_pool_size == 5
        assert settings.db_max_overflow == 5

    def test_custom_values(self) -> None:
        """可配置自定义 pool_size 和 max_overflow。"""
        settings = _make_settings(db_pool_size=10, db_max_overflow=20)
        assert settings.db_pool_size == 10
        assert settings.db_max_overflow == 20

    def test_pool_size_must_be_positive(self) -> None:
        """pool_size 必须 >= 1。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_settings(db_pool_size=0)

    def test_max_overflow_must_be_non_negative(self) -> None:
        """max_overflow 必须 >= 0。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_settings(db_max_overflow=-1)

    def test_pool_size_in_safe_summary(self) -> None:
        """to_safe_summary 包含连接池配置。"""
        settings = _make_settings(db_pool_size=8, db_max_overflow=3)
        summary = settings.to_safe_summary()
        assert summary["db_pool_size"] == "8"
        assert summary["db_max_overflow"] == "3"
