"""健康检查集成测试（SPEC §6.2、§9.5）。

覆盖验收条件：
- GET /health/live 在 DB 不可用时仍返回 200
- GET /health/ready 通过 provider 接口检查 DB 连接；失败返回 503；恢复后无需重启即可重新就绪
- Request ID 每请求生成，写入响应头 X-Request-ID
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.app import create_app
from app.config.settings import AppEnv, Settings
from app.health.providers import DbPoolProvider, ReadinessProbe

pytestmark = [pytest.mark.integration, pytest.mark.g1]

# 测试用有效密钥（64 位 hex = 32 字节，字节值多样，非退化密钥）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"


def _make_test_settings() -> Settings:
    """构造测试用 Settings（禁用 .env 加载，使用 testing 环境）。"""
    return Settings(
        _env_file=None,
        app_env=AppEnv.TESTING,
        database_url="postgresql+psycopg://apex:secret@localhost:5432/apex_admin_test",
        access_token_hmac_key=_VALID_ACCESS_KEY,
        refresh_token_hmac_key=_VALID_REFRESH_KEY,
        config_encryption_key=_VALID_ENCRYPTION_KEY,
        file_storage_root="/tmp/apex-test-files",
    )


class FakeDbPoolProvider(DbPoolProvider):
    """测试用的假数据库连接池 provider。

    通过 ``connected`` 属性控制数据库连通性，用于测试就绪检查的 200/503 行为。
    """

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.initialized = False
        self.disposed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def dispose(self) -> None:
        self.disposed = True

    async def check_connection(self) -> bool:
        return self.connected


class FakeRevisionProbe(ReadinessProbe):
    """测试用的假 Alembic revision 探针。"""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def probe(self) -> bool:
        return self.ready


# ---------------------------------------------------------------------------
# 存活检查（验收条件：GET /health/live 在 DB 不可用时仍返回 200）
# ---------------------------------------------------------------------------


class TestHealthLive:
    """验证存活检查不依赖数据库。"""

    def test_live_returns_200_without_db(self) -> None:
        """未配置数据库 provider 时存活检查仍返回 200。"""
        app = create_app(settings=_make_test_settings())
        with TestClient(app) as client:
            response = client.get("/health/live")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_live_returns_200_when_db_unavailable(self) -> None:
        """数据库不可用时存活检查仍返回 200。"""
        provider = FakeDbPoolProvider(connected=False)
        app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
        with TestClient(app) as client:
            response = client.get("/health/live")
            assert response.status_code == 200

    def test_live_returns_200_when_db_available(self) -> None:
        """数据库可用时存活检查返回 200。"""
        provider = FakeDbPoolProvider(connected=True)
        app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
        with TestClient(app) as client:
            response = client.get("/health/live")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# 就绪检查（验收条件：GET /health/ready 通过 provider 检查 DB；失败 503；恢复后无需重启）
# ---------------------------------------------------------------------------


class TestHealthReady:
    """验证就绪检查通过 provider 接口检查 DB 连接。"""

    def test_ready_returns_200_when_db_available(self) -> None:
        """数据库可用时就绪检查返回 200。"""
        provider = FakeDbPoolProvider(connected=True)
        app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
        with TestClient(app) as client:
            response = client.get("/health/ready")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    def test_ready_returns_503_when_db_unavailable(self) -> None:
        """数据库不可用时就绪检查返回 503。"""
        provider = FakeDbPoolProvider(connected=False)
        app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
        with TestClient(app) as client:
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["status"] == "not_ready"

    def test_ready_returns_503_when_revision_mismatch(self) -> None:
        """Alembic revision 不一致时就绪检查返回 503。"""
        provider = FakeDbPoolProvider(connected=True)
        probe = FakeRevisionProbe(ready=False)
        app = create_app(
            settings=_make_test_settings(),
            db_pool_provider=provider,
            revision_probe=probe,
        )
        with TestClient(app) as client:
            response = client.get("/health/ready")
            assert response.status_code == 503

    def test_ready_recovers_without_restart(self) -> None:
        """数据库恢复后就绪检查无需重启即可返回 200。"""
        provider = FakeDbPoolProvider(connected=False)
        app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
        with TestClient(app) as client:
            # 初始不可用
            response = client.get("/health/ready")
            assert response.status_code == 503

            # 模拟数据库恢复
            provider.connected = True

            # 无需重启，再次检查即恢复
            response = client.get("/health/ready")
            assert response.status_code == 200

    def test_ready_without_providers_returns_200(self) -> None:
        """未配置任何 provider 时就绪检查返回 200（无检查条件）。"""
        app = create_app(settings=_make_test_settings())
        with TestClient(app) as client:
            response = client.get("/health/ready")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Request ID 响应头（验收条件：Request ID 写入响应头 X-Request-ID）
# ---------------------------------------------------------------------------


class TestRequestIdHeader:
    """验证每个响应包含 X-Request-ID 头。"""

    def test_response_has_request_id(self) -> None:
        """响应头包含 X-Request-ID。"""
        app = create_app(settings=_make_test_settings())
        with TestClient(app) as client:
            response = client.get("/health/live")
            assert response.status_code == 200
            assert "x-request-id" in response.headers
            # 自动生成的 Request ID 是 UUID4 格式
            request_id = response.headers["x-request-id"]
            assert len(request_id) > 0

    def test_client_provided_request_id_is_echoed(self) -> None:
        """客户端提供的 X-Request-ID 被回显到响应头。"""
        app = create_app(settings=_make_test_settings())
        with TestClient(app) as client:
            response = client.get("/health/live", headers={"X-Request-ID": "my-req-id"})
            assert response.status_code == 200
            assert response.headers["x-request-id"] == "my-req-id"

    def test_different_requests_get_different_ids(self) -> None:
        """不同请求获得不同的 Request ID（未提供时自动生成）。"""
        app = create_app(settings=_make_test_settings())
        with TestClient(app) as client:
            r1 = client.get("/health/live")
            r2 = client.get("/health/live")
            assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


# ---------------------------------------------------------------------------
# Lifespan 验证（验收条件：DB 池初始化委托给 provider 接口）
# ---------------------------------------------------------------------------


class TestLifespanProviderIntegration:
    """验证 Lifespan 通过 provider 接口管理 DB 池生命周期。"""

    def test_db_pool_initialized_on_startup(self) -> None:
        """启动时调用 provider 的 initialize 方法。"""
        provider = FakeDbPoolProvider(connected=True)
        app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
        with TestClient(app):
            assert provider.initialized is True

    def test_db_pool_disposed_on_shutdown(self) -> None:
        """关闭时调用 provider 的 dispose 方法。"""
        provider = FakeDbPoolProvider(connected=True)
        app = create_app(settings=_make_test_settings(), db_pool_provider=provider)
        with TestClient(app):
            assert provider.initialized is True
        assert provider.disposed is True

    def test_no_error_without_db_provider(self) -> None:
        """未提供 DB provider 时应用正常启动和关闭。"""
        app = create_app(settings=_make_test_settings())
        with TestClient(app) as client:
            response = client.get("/health/live")
            assert response.status_code == 200
