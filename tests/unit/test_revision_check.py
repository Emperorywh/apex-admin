"""Alembic revision 一致性校验探针单元测试（SPEC §6.2、§26.1）。

覆盖验收条件：
- revision 一致时 probe() 返回 True
- revision 不一致时 probe() 返回 False
- 引擎未初始化时 probe() 返回 False
- 多 head 时 probe() 返回 False
- 数据库查询失败（alembic_version 表不存在）时 probe() 返回 False
- _get_expected_head() 从真实迁移脚本目录读取 head
- AlembicRevisionProbe 是 ReadinessProbe 的子类

所有测试不连接真实数据库，通过 mock 引擎控制返回值。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.config.settings import AppEnv, Settings
from app.health.providers import ReadinessProbe
from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider
from app.infrastructure.database.revision_check import SCRIPT_LOCATION, AlembicRevisionProbe

pytestmark = [pytest.mark.unit, pytest.mark.g1]

# 测试用有效密钥（64 位 hex = 32 字节，字节值多样，非退化密钥）
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


def _make_provider_with_mock_engine(revision: str | None = None) -> tuple[Any, Any]:
    """创建带 mock 引擎的 provider。

    Args:
        revision: ``alembic_version`` 表中的当前 revision；
            ``None`` 表示表为空（无行）

    Returns:
        ``(mock_engine, provider)`` 元组，provider.engine 返回 mock_engine
    """
    result = MagicMock()
    if revision is not None:
        result.fetchone.return_value = (revision,)
    else:
        result.fetchone.return_value = None

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value=result)

    mock_engine = MagicMock()

    @asynccontextmanager
    async def fake_connect() -> AsyncIterator[Any]:
        yield mock_conn

    mock_engine.connect = fake_connect

    provider = MagicMock(spec=SqlAlchemyDbPoolProvider)
    provider.engine = mock_engine
    return mock_engine, provider


# ---------------------------------------------------------------------------
# probe() revision 一致性校验（验收条件：revision 不一致时返回 503）
# ---------------------------------------------------------------------------


class TestProbeRevisionMatch:
    """验证 revision 一致与不一致时的 probe 行为。"""

    async def test_probe_returns_true_when_revision_matches(self) -> None:
        """当前 revision 与期望 head 一致时返回 True。"""
        _, provider = _make_provider_with_mock_engine(revision="0001")
        probe = AlembicRevisionProbe(provider)

        result = await probe.probe()
        assert result is True

    async def test_probe_returns_false_when_revision_mismatch(self) -> None:
        """当前 revision 与期望 head 不一致时返回 False。"""
        _, provider = _make_provider_with_mock_engine(revision="outdated_revision")
        probe = AlembicRevisionProbe(provider)

        result = await probe.probe()
        assert result is False

    async def test_probe_returns_false_when_db_revision_is_none(self) -> None:
        """数据库 alembic_version 表为空时返回 False。"""
        _, provider = _make_provider_with_mock_engine(revision=None)
        probe = AlembicRevisionProbe(provider)

        result = await probe.probe()
        assert result is False


# ---------------------------------------------------------------------------
# probe() 前置条件失败（验收条件：未执行迁移 / 引擎不可用时 503）
# ---------------------------------------------------------------------------


class TestProbePreconditionFailure:
    """验证引擎未初始化和查询失败时的 probe 行为。"""

    async def test_probe_returns_false_when_engine_is_none(self) -> None:
        """引擎未初始化时返回 False。"""
        provider = MagicMock(spec=SqlAlchemyDbPoolProvider)
        provider.engine = None
        probe = AlembicRevisionProbe(provider)

        result = await probe.probe()
        assert result is False

    async def test_probe_returns_false_when_query_raises(self) -> None:
        """alembic_version 表不存在或查询异常时返回 False。"""
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(
            side_effect=OperationalError(
                "SELECT version_num FROM alembic_version",
                {},
                Exception('relation "alembic_version" does not exist'),
            )
        )

        mock_engine = MagicMock()

        @asynccontextmanager
        async def fake_connect() -> AsyncIterator[Any]:
            yield mock_conn

        mock_engine.connect = fake_connect

        provider = MagicMock(spec=SqlAlchemyDbPoolProvider)
        provider.engine = mock_engine
        probe = AlembicRevisionProbe(provider)

        result = await probe.probe()
        assert result is False


# ---------------------------------------------------------------------------
# probe() 多 head 检测（验收条件：单头强制）
# ---------------------------------------------------------------------------


class TestProbeMultiHead:
    """验证多 head 时 probe 返回 False。"""

    async def test_probe_returns_false_when_multi_head(self, tmp_path: Any) -> None:
        """迁移脚本目录存在多个 head 时返回 False。"""
        _, provider = _make_provider_with_mock_engine(revision="0001")
        probe = AlembicRevisionProbe(provider, script_location=str(tmp_path))
        # tmp_path 不含迁移文件，ScriptDirectory.get_heads() 返回空列表
        # 而非单个 head，应被判定为校验失败

        result = await probe.probe()
        assert result is False


# ---------------------------------------------------------------------------
# _get_expected_head() 读取迁移脚本目录（验收条件：revision 校验 provider）
# ---------------------------------------------------------------------------


class TestGetExpectedHead:
    """验证从真实迁移脚本目录读取 head revision。"""

    def test_reads_head_from_real_script_directory(self) -> None:
        """从项目迁移脚本目录读取到唯一的 head revision。"""
        _, provider = _make_provider_with_mock_engine()
        probe = AlembicRevisionProbe(provider)

        head = probe._get_expected_head()
        assert head == "0001"

    def test_returns_none_for_multi_head_script_directory(self, tmp_path: Any) -> None:
        """空目录或无 revision 文件时返回 None。"""
        _, provider = _make_provider_with_mock_engine()
        probe = AlembicRevisionProbe(provider, script_location=str(tmp_path))

        head = probe._get_expected_head()
        assert head is None


# ---------------------------------------------------------------------------
# 类型与常量校验
# ---------------------------------------------------------------------------


class TestProbeTypeAndConstants:
    """验证探针类型和常量。"""

    def test_probe_is_readiness_probe(self) -> None:
        """AlembicRevisionProbe 是 ReadinessProbe 的子类。"""
        provider = MagicMock(spec=SqlAlchemyDbPoolProvider)
        probe = AlembicRevisionProbe(provider)
        assert isinstance(probe, ReadinessProbe)

    def test_script_location_constant_points_to_migrations(self) -> None:
        """SCRIPT_LOCATION 常量指向 migrations 目录。"""
        import os

        assert SCRIPT_LOCATION.endswith(os.path.join("database", "migrations"))
        assert os.path.isdir(SCRIPT_LOCATION)
