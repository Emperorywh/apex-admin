"""应用入口测试 — SPEC 6.1 / 9.5.

覆盖:
  - 导入 app.main 不触发数据库或网络副作用。
  - lifespan 启动/关闭钩子可测。
  - meta 端点返回应用名称/版本/环境且为显式公共端点。
  - Request ID 无入站头时生成、响应头回写。
  - Request ID 从入站头继承。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from starlette.testclient import TestClient

from app.api.meta import MetaResponse
from app.core.config import Settings
from app.main import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI

# ── 辅助函数 ───────────────────────────────────────────────────────────────


def _make_test_app() -> FastAPI:
    """创建测试用应用实例（开发环境默认配置）。"""

    return create_app()


# ── 导入无副作用 ───────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_import_main_no_side_effects() -> None:
    """导入 app.main 不触发数据库或网络副作用（SPEC 6.1）.

    验证 create_app 是可调用对象而非已执行的结果，
    且模块导入不产生 Settings 实例或数据库连接。
    """

    # 重新导入 app.main 不应抛出异常
    import app.main  # noqa: F401

    # create_app 和 lifespan 都是可调用对象，不是已调用的结果
    assert callable(app.main.create_app)
    assert callable(app.main.lifespan)

    # 导入不创建全局 Settings 实例（main 模块无 settings 属性）
    assert not hasattr(app.main, "settings")
    assert not hasattr(app.main, "app")


# ── Lifespan ──────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
async def test_lifespan_startup_shutdown_hooks() -> None:
    """lifespan 启动/关闭钩子可测（SPEC 6.1）.

    通过 TestClient 的上下文管理器触发 lifespan，
    验证 startup 和 shutdown 事件按序执行。
    """

    app = _make_test_app()
    with TestClient(app) as client:
        # startup 已执行
        assert "startup" in app.state.lifecycle_events
        # meta 端点可访问
        response = client.get("/api/v1/meta")
        assert response.status_code == 200

    # 退出上下文后 shutdown 已执行
    assert "shutdown" in app.state.lifecycle_events
    assert app.state.lifecycle_events == ["startup", "shutdown"]


# ── Meta 端点 ──────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_meta_endpoint_returns_app_info() -> None:
    """meta 端点返回应用名称/版本/环境（SPEC 6.1）。"""

    app = _make_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/meta")

    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data
    assert "environment" in data


@pytest.mark.g1
@pytest.mark.unit
def test_meta_endpoint_values_match_settings() -> None:
    """meta 端点返回值与 Settings 一致。"""

    settings = Settings(
        APP_NAME="test-app",
        APP_VERSION="9.9.9",
        ENVIRONMENT="testing",
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/meta")

    data = response.json()
    assert data["name"] == "test-app"
    assert data["version"] == "9.9.9"
    assert data["environment"] == "testing"


@pytest.mark.g1
@pytest.mark.unit
def test_meta_response_model_excludes_sensitive_fields() -> None:
    """MetaResponse 不含敏感字段。"""

    fields = set(MetaResponse.model_fields.keys())
    assert fields == {"name", "version", "environment"}


# ── Request ID 中间件 ──────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_request_id_generated_when_no_header() -> None:
    """无入站 Request ID 头时自动生成并回写响应头（SPEC 9.5）。"""

    app = _make_test_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/meta")

    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    assert len(request_id) > 0


@pytest.mark.g1
@pytest.mark.unit
def test_request_id_from_inbound_header() -> None:
    """有入站 Request ID 头时继承并回写响应头（SPEC 9.5）。"""

    app = _make_test_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/meta",
            headers={"X-Request-ID": "inbound-req-id"},
        )

    assert response.headers["X-Request-ID"] == "inbound-req-id"


@pytest.mark.g1
@pytest.mark.unit
def test_request_id_unique_per_request() -> None:
    """无入站头时每个请求获得不同的 Request ID。"""

    app = _make_test_app()
    with TestClient(app) as client:
        resp1 = client.get("/api/v1/meta")
        resp2 = client.get("/api/v1/meta")

    id1 = resp1.headers["X-Request-ID"]
    id2 = resp2.headers["X-Request-ID"]
    assert id1 != id2
