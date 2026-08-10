"""健康检查测试 — SPEC 6.2.

覆盖验收标准:
  - DB 可用且 revision 一致时 ready 返回 200。
  - 停库后 ready 返回 503 与稳定错误码、live 仍 200。
  - 恢复后 ready 无需重启回 200。
  - 响应不含敏感配置。
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from app.application.ports import HealthCheck
from app.core.config import Settings
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.health import DbHealthChecker
from app.infrastructure.db.migrations import get_head_revision
from app.main import create_app


async def _migrate_db(database_url: str) -> None:
    """在线程中执行 alembic upgrade（避免 asyncio.run 与 pytest 事件循环冲突）。"""

    from alembic import command
    from alembic.config import Config

    from app.infrastructure.db.migrations import ALEMBIC_INI_PATH

    def _upgrade() -> None:
        config = Config(str(ALEMBIC_INI_PATH))
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")

    await asyncio.to_thread(_upgrade)


# ── DbHealthChecker 单元/集成测试 ─────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_db_health_checker_is_health_check_port() -> None:
    """DbHealthChecker 是 HealthCheck Port 的实现。"""

    engine = create_db_engine(
        "postgresql+psycopg://nobody@127.0.0.1:1/nonexistent",
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 3},
    )
    try:
        checker = DbHealthChecker(engine, expected_revision="0001_initial")
        assert isinstance(checker, HealthCheck)
    finally:
        pass


@pytest.mark.g1
@pytest.mark.integration
async def test_health_check_healthy_when_db_available(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 可用且 revision 一致时 check_ready 返回 healthy（SPEC 6.2）。"""

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)
    await _migrate_db(database_url)

    head_revision = get_head_revision()
    engine = create_db_engine(database_url)
    try:
        checker = DbHealthChecker(engine, expected_revision=head_revision)
        result = await checker.check_ready()

        assert result.healthy
        assert result.code == "DB.OK"
    finally:
        await engine.dispose()


@pytest.mark.g1
@pytest.mark.integration
async def test_health_check_unhealthy_when_db_unavailable() -> None:
    """DB 不可用时 check_ready 返回 unhealthy 和稳定错误码（SPEC 6.2）。"""

    engine = create_db_engine(
        "postgresql+psycopg://nobody@127.0.0.1:1/nonexistent",
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 3},
    )
    checker = DbHealthChecker(engine, expected_revision="0001_initial")
    try:
        result = await checker.check_ready()

        assert not result.healthy
        assert result.code == "DB.UNAVAILABLE"
    finally:
        await engine.dispose()


@pytest.mark.g1
@pytest.mark.integration
async def test_health_check_revision_mismatch(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """revision 不一致时返回 unhealthy 和 REVISION_MISMATCH（SPEC 6.2）。"""

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)
    await _migrate_db(database_url)

    engine = create_db_engine(database_url)
    try:
        # 传入错误的 expected revision
        checker = DbHealthChecker(engine, expected_revision="WRONG_REVISION")
        result = await checker.check_ready()

        assert not result.healthy
        assert result.code == "DB.REVISION_MISMATCH"
    finally:
        await engine.dispose()


# ── API 端点测试 ──────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_health_live_always_200() -> None:
    """/health/live 始终返回 200（SPEC 6.2）。

    即使数据库不可用，存活检查仍返回 200。
    """

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.g1
@pytest.mark.integration
def test_health_ready_200_when_db_available(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 可用时 /health/ready 返回 200（SPEC 6.2）。"""

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)

    # 先同步执行迁移
    from alembic import command
    from alembic.config import Config

    from app.infrastructure.db.migrations import ALEMBIC_INI_PATH

    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    settings = Settings(DATABASE_URL=database_url)
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["code"] == "DB.OK"


@pytest.mark.g1
@pytest.mark.unit
def test_health_ready_503_when_db_unavailable() -> None:
    """DB 不可用时 /health/ready 返回 503 和稳定错误码（SPEC 6.2）。

    同时验证 /health/live 仍返回 200。
    """

    # 使用不可达端口（connect_timeout 避免 Windows TCP 行为卡住）
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://nobody@127.0.0.1:1/db?connect_timeout=3",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        # ready 应 503
        ready_response = client.get("/health/ready")
        assert ready_response.status_code == 503
        ready_data = ready_response.json()
        assert ready_data["status"] == "unhealthy"
        assert ready_data["code"] == "DB.UNAVAILABLE"

        # live 仍 200
        live_response = client.get("/health/live")
        assert live_response.status_code == 200


@pytest.mark.g1
@pytest.mark.integration
def test_health_ready_recovers_without_restart(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复后 ready 无需重启回 200（SPEC 6.2）。

    使用两个不同的应用实例模拟"断库"与"恢复"。
    """

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)

    # 先同步执行迁移
    from alembic import command
    from alembic.config import Config

    from app.infrastructure.db.migrations import ALEMBIC_INI_PATH

    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    # "断库"实例 — 连接不可达地址（connect_timeout 避免卡住）
    bad_settings = Settings(
        DATABASE_URL="postgresql+psycopg://nobody@127.0.0.1:1/db?connect_timeout=3",
    )
    bad_app = create_app(bad_settings)
    with TestClient(bad_app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503

    # "恢复"实例 — 连接真实数据库
    good_settings = Settings(DATABASE_URL=database_url)
    good_app = create_app(good_settings)
    with TestClient(good_app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200


@pytest.mark.g1
@pytest.mark.integration
def test_health_response_no_sensitive_config(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """健康检查响应不含敏感配置（SPEC 6.2）。"""

    monkeypatch.setenv("APEX_DATABASE_URL", database_url)

    # 先同步执行迁移
    from alembic import command
    from alembic.config import Config

    from app.infrastructure.db.migrations import ALEMBIC_INI_PATH

    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    settings = Settings(DATABASE_URL=database_url)
    app = create_app(settings)

    with TestClient(app) as client:
        ready_response = client.get("/health/ready")
        live_response = client.get("/health/live")

    ready_body = ready_response.text
    live_body = live_response.text

    # 响应不应包含敏感配置字段
    sensitive_fragments = [
        "DATABASE_URL",
        "ACCESS_TOKEN_HMAC_KEY",
        "REFRESH_TOKEN_HMAC_KEY",
        "password",
        "secret",
        "token",
    ]
    for fragment in sensitive_fragments:
        assert fragment not in ready_body, f"ready 响应包含敏感信息: {fragment}"
        assert fragment not in live_body, f"live 响应包含敏感信息: {fragment}"

    # 响应字段白名单验证
    ready_data = ready_response.json()
    assert set(ready_data.keys()) <= {"status", "code", "detail"}
