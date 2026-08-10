"""OpenAPI 文档定制测试 — SPEC 9.6 / 28.4.

覆盖:
  - OpenAPI 按模块 tag 分组。
  - operationId 全局唯一。
  - JSON 快照测试（与 tests/snapshots/openapi.json 比较）。
  - 生产环境可通过配置关闭文档。
  - 文档不暴露敏感信息。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.core.api.openapi import OPENAPI_TAGS
from app.core.config import Environment, Settings
from app.main import create_app

# ── OpenAPI Tags 分组 ─────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_openapi_tags_defined_by_module() -> None:
    """OpenAPI tags 按模块分组，每个 tag 有描述（SPEC 9.6）。"""

    tag_names = {t["name"] for t in OPENAPI_TAGS}
    # 现有路由使用的 tag（health、meta）必须声明
    assert "health" in tag_names
    assert "meta" in tag_names

    # 每个 tag 都有中文描述
    for tag in OPENAPI_TAGS:
        assert tag["description"], f"tag {tag['name']} 缺少描述"


@pytest.mark.g1
@pytest.mark.api
def test_openapi_tags_appear_in_schema() -> None:
    """生成的 OpenAPI schema 包含 tags 元数据。"""

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    schema: dict[str, Any] = response.json()
    tag_names_in_schema = {t["name"] for t in schema.get("tags", [])}
    assert "health" in tag_names_in_schema
    assert "meta" in tag_names_in_schema


# ── operationId 全局唯一 ──────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.api
def test_operation_ids_globally_unique() -> None:
    """OpenAPI 中每个 operationId 全局唯一（SPEC 9.6 / 28.4）。

    SPEC 28.4: "测试 OpenAPI Operation ID 唯一并与版本控制中的快照一致"。
    """

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    schema: dict[str, Any] = response.json()

    # 收集所有路径下所有方法的 operationId
    operation_ids: list[str] = []
    for path_data in schema.get("paths", {}).values():
        for method_data in path_data.values():
            if isinstance(method_data, dict) and "operationId" in method_data:
                operation_ids.append(method_data["operationId"])

    assert len(operation_ids) > 0, "未找到任何 operationId"

    # 检查全局唯一性
    duplicates = {oid for oid in operation_ids if operation_ids.count(oid) > 1}
    assert not duplicates, f"operationId 重复: {duplicates}"


# ── JSON 快照测试 ─────────────────────────────────────────────────────────

_SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "openapi.json"


@pytest.mark.g1
@pytest.mark.api
def test_openapi_matches_snapshot() -> None:
    """生成的 OpenAPI JSON 与版本控制中的快照一致（SPEC 9.6 / 28.4）。

    SPEC 28.4: "测试 OpenAPI Operation ID 唯一并与版本控制中的快照一致"。
    快照文件位于 tests/snapshots/openapi.json。

    如果 OpenAPI 输出有意变更，需更新快照并评审。
    """

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    actual: dict[str, Any] = response.json()

    assert _SNAPSHOT_PATH.exists(), (
        f"快照文件不存在: {_SNAPSHOT_PATH}。如果这是首次运行，请生成快照并提交。"
    )

    expected = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert actual == expected, (
        "OpenAPI JSON 与快照不一致。如果变更是有意的，"
        "请更新 tests/snapshots/openapi.json。"
    )


# ── 生产环境关闭文档 ──────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_production_disables_docs() -> None:
    """生产环境默认关闭 API 文档端点（SPEC 9.6）。

    生产环境且未显式启用文档时，/docs、/redoc、/openapi.json 返回 404。
    """

    settings = Settings(
        ENVIRONMENT=Environment.PRODUCTION,
        ENABLE_API_DOCS=False,
        DATABASE_URL="postgresql+psycopg://apex@127.0.0.1:55432/postgres",
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


@pytest.mark.g1
@pytest.mark.unit
def test_production_explicitly_enables_docs() -> None:
    """生产环境显式 ENABLE_API_DOCS=true 可开启文档（SPEC 9.6）。"""

    settings = Settings(
        ENVIRONMENT=Environment.PRODUCTION,
        ENABLE_API_DOCS=True,
        DATABASE_URL="postgresql+psycopg://apex@127.0.0.1:55432/postgres",
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


@pytest.mark.g1
@pytest.mark.unit
def test_development_docs_enabled_by_default() -> None:
    """开发环境默认开启 API 文档（SPEC 9.6）。"""

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200


# ── 文档不暴露敏感信息 ────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.api
def test_openapi_does_not_leak_sensitive_info() -> None:
    """OpenAPI 文档不暴露内部密钥、数据库信息（SPEC 9.6）。"""

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    body_text = json.dumps(response.json())
    # 不含数据库连接串
    assert "DATABASE_URL" not in body_text
    assert "postgresql" not in body_text.lower()
    # 不含密钥关键字
    assert "HMAC" not in body_text
    assert "secret" not in body_text.lower()
    # 不含 password 关键字
    assert "password" not in body_text.lower()
