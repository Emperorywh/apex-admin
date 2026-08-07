"""OpenAPI 文档与契约测试（SPEC §9.6、§34.1、§28.4）。

覆盖验收条件：
- OpenAPI 按模块标签分组操作
- 每个操作有摘要和说明
- 每个 Operation ID 全局唯一且稳定
- 快照文件 tests/fixtures/openapi.json 存在；CI 比较生成 JSON 与快照
- 生产模式可禁用或限制文档
- 文档不暴露内部密钥、数据库信息或堆栈
- Bearer 认证方案集成到 OpenAPI 安全方案
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.openapi import SECURITY_SCHEME_NAME
from app.app import create_app
from app.config.settings import AppEnv, Settings

pytestmark = [pytest.mark.api, pytest.mark.g1]

# 测试用有效密钥（64 位 hex = 32 字节，字节值多样，非退化密钥）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"

# OpenAPI 快照文件路径（SPEC §34.1：CI 比较生成 JSON 与快照）
_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "openapi.json"

# OpenAPI path item 中可包含的 HTTP 方法
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})

# 文档中不得出现的敏感关键词（SPEC §9.6：不暴露内部密钥、数据库信息和堆栈）
_SENSITIVE_PATTERNS = frozenset(
    {
        "password",
        "secret",
        "token_hmac",
        "encryption_key",
        "database_url",
        "dsn",
        "connection_string",
        "traceback",
        "stack",
        "psycopg://",
        "postgresql+psycopg",
        "access_token_hmac_key",
        "refresh_token_hmac_key",
        "config_encryption_key",
    }
)


def _make_test_settings(env: AppEnv = AppEnv.TESTING) -> Settings:
    """构造测试用 Settings（禁用 .env 加载）。

    Args:
        env: 运行环境，默认 TESTING；生产环境验证文档禁用行为时使用 PRODUCTION
    """
    kwargs: dict[str, Any] = dict(
        _env_file=None,
        app_env=env,
        database_url="postgresql+psycopg://apex:secret@localhost:5432/apex_admin_test",
        access_token_hmac_key=_VALID_ACCESS_KEY,
        refresh_token_hmac_key=_VALID_REFRESH_KEY,
        config_encryption_key=_VALID_ENCRYPTION_KEY,
        file_storage_root="/tmp/apex-test-files",
    )
    # 生产环境需要显式配置 CORS 来源（SPEC §23.1）
    if env == AppEnv.PRODUCTION:
        kwargs["allowed_origins"] = ["https://example.com"]
    return Settings(**kwargs)


@pytest.fixture
def openapi_schema() -> dict[str, Any]:
    """创建应用并返回生成的 OpenAPI schema。"""
    app = create_app(settings=_make_test_settings())
    return app.openapi()


def _iter_operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """遍历 OpenAPI schema 中的所有操作。

    Returns:
        [(path, method, operation_dict), ...] 列表
    """
    operations: list[tuple[str, str, dict[str, Any]]] = []
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict):
                operations.append((path, method, operation))
    return operations


# ===========================================================================
# 验收条件 0：OpenAPI 按模块标签分组操作（SPEC §9.6）
# ===========================================================================


class TestTagGrouping:
    """验证 OpenAPI 操作按模块标签分组。"""

    def test_all_operations_have_tags(self, openapi_schema: dict[str, Any]) -> None:
        """每个操作都携带至少一个 tag，用于文档分组。"""
        operations = _iter_operations(openapi_schema)
        assert len(operations) > 0, "OpenAPI 应至少包含一个操作"

        for path, method, operation in operations:
            tags = operation.get("tags")
            assert tags is not None, f"{method.upper()} {path} 缺少 tags 字段"
            assert len(tags) > 0, f"{method.upper()} {path} 的 tags 为空列表"

    def test_health_endpoints_grouped_under_health_tag(
        self,
        openapi_schema: dict[str, Any],
    ) -> None:
        """健康检查端点归类到 "health" 标签下。"""
        for path, method, operation in _iter_operations(openapi_schema):
            if "/health/" in path:
                assert "health" in operation["tags"], f"{method.upper()} {path} 应归属 health 标签"

    def test_tags_are_consistent_across_paths(self, openapi_schema: dict[str, Any]) -> None:
        """相同模块前缀的路径使用一致的标签。"""
        # 按标签分组路径，验证同一标签下的路径一致
        tag_to_paths: dict[str, list[str]] = {}
        for path, _method, operation in _iter_operations(openapi_schema):
            for tag in operation.get("tags", []):
                tag_to_paths.setdefault(tag, []).append(path)

        # health 标签下的所有路径都应以 /health 开头
        health_paths = tag_to_paths.get("health", [])
        for p in health_paths:
            assert p.startswith("/health"), f"health 标签下的路径 {p} 不以 /health 开头"


# ===========================================================================
# 验收条件 1：每个操作有摘要和说明（SPEC §9.6）
# ===========================================================================


class TestOperationMetadata:
    """验证每个操作具有摘要（summary）和说明（description）。"""

    def test_all_operations_have_summary(self, openapi_schema: dict[str, Any]) -> None:
        """每个操作都有非空 summary。"""
        for path, method, operation in _iter_operations(openapi_schema):
            summary = operation.get("summary")
            assert summary is not None, f"{method.upper()} {path} 缺少 summary"
            assert len(summary) > 0, f"{method.upper()} {path} 的 summary 为空"

    def test_all_operations_have_description(self, openapi_schema: dict[str, Any]) -> None:
        """每个操作都有非空 description。"""
        for path, method, operation in _iter_operations(openapi_schema):
            description = operation.get("description")
            assert description is not None, f"{method.upper()} {path} 缺少 description"
            assert len(description) > 0, f"{method.upper()} {path} 的 description 为空"


# ===========================================================================
# 验收条件 2：Operation ID 全局唯一且稳定（SPEC §9.6）
# ===========================================================================


class TestOperationIdUniqueness:
    """验证 Operation ID 全局唯一。"""

    def test_operation_ids_are_unique(self, openapi_schema: dict[str, Any]) -> None:
        """所有 Operation ID 互不重复。"""
        operation_ids = [
            op["operationId"]
            for _path, _method, op in _iter_operations(openapi_schema)
            if "operationId" in op
        ]
        assert len(operation_ids) > 0, "应至少有一个 Operation ID"

        duplicates = {op_id for op_id in operation_ids if operation_ids.count(op_id) > 1}
        assert len(duplicates) == 0, f"重复的 Operation ID: {duplicates}"

    def test_duplicate_operation_ids_raises_value_error(self) -> None:
        """自定义 schema 生成检测到重复 Operation ID 时抛出 ValueError。

        通过构造含重复 ID 的 schema 片段验证 _assert_unique_operation_ids。
        """
        from app.api.openapi import _assert_unique_operation_ids

        schema_with_dupes: dict[str, Any] = {
            "paths": {
                "/a": {"get": {"operationId": "dup_id"}},
                "/b": {"get": {"operationId": "dup_id"}},
            },
        }
        with pytest.raises(ValueError, match="Operation ID 重复"):
            _assert_unique_operation_ids(schema_with_dupes)

    def test_unique_operation_ids_pass_validation(self) -> None:
        """不重复的 Operation ID 通过校验。"""
        from app.api.openapi import _assert_unique_operation_ids

        schema_ok: dict[str, Any] = {
            "paths": {
                "/a": {"get": {"operationId": "id_a"}},
                "/b": {"post": {"operationId": "id_b"}},
            },
        }
        _assert_unique_operation_ids(schema_ok)  # 不应抛出异常


class TestOperationIdStability:
    """验证 Operation ID 稳定（同一应用配置多次生成结果一致）。"""

    def test_operation_ids_stable_across_instances(self) -> None:
        """多次创建应用实例，Operation ID 保持一致。"""
        schema_a = create_app(settings=_make_test_settings()).openapi()
        schema_b = create_app(settings=_make_test_settings()).openapi()

        ids_a = {
            op["operationId"]
            for _path, _method, op in _iter_operations(schema_a)
            if "operationId" in op
        }
        ids_b = {
            op["operationId"]
            for _path, _method, op in _iter_operations(schema_b)
            if "operationId" in op
        }

        assert ids_a == ids_b, "多次创建应用实例后 Operation ID 不一致"


# ===========================================================================
# 验收条件 3：快照文件存在；CI 比较生成 JSON 与快照（SPEC §34.1）
# ===========================================================================


class TestSnapshotConsistency:
    """验证 OpenAPI JSON 与版本控制中的快照一致（SPEC §34.1）。"""

    def test_snapshot_file_exists(self) -> None:
        """快照文件 tests/fixtures/openapi.json 存在。"""
        assert _SNAPSHOT_PATH.is_file(), f"快照文件不存在: {_SNAPSHOT_PATH}"

    def test_generated_schema_matches_snapshot(self, openapi_schema: dict[str, Any]) -> None:
        """生成的 OpenAPI JSON 与快照文件完全一致。

        快照是活的契约：端点变更时需显式更新快照（SPEC §34.1）。
        如果此测试失败，说明 OpenAPI schema 发生了变更，请检查变更是否有意，
        并在有意时更新快照文件。
        """
        assert _SNAPSHOT_PATH.is_file(), "快照文件不存在"

        snapshot_text = _SNAPSHOT_PATH.read_text(encoding="utf-8")
        snapshot_schema = json.loads(snapshot_text)

        assert openapi_schema == snapshot_schema, (
            "生成的 OpenAPI schema 与快照不一致。\n"
            "如果变更是有意的，请更新 tests/fixtures/openapi.json：\n"
            '  uv run python -c "..."  # 重新生成快照\n'
            "如果变更不是有意的，请检查是否无意修改了路由或 schema 定义。"
        )


# ===========================================================================
# 验收条件 4：生产模式可禁用或限制文档（SPEC §9.6）
# ===========================================================================


class TestProductionDocsControl:
    """验证生产环境可禁用交互式文档。"""

    def test_development_environment_has_docs(self) -> None:
        """非生产环境提供 Swagger UI、ReDoc 和 OpenAPI JSON。"""
        app = create_app(settings=_make_test_settings(env=AppEnv.DEVELOPMENT))
        assert app.docs_url is not None, "开发环境应提供 Swagger UI"
        assert app.redoc_url is not None, "开发环境应提供 ReDoc"
        assert app.openapi_url is not None, "开发环境应提供 OpenAPI JSON"

    def test_production_environment_disables_docs(self) -> None:
        """生产环境禁用 Swagger UI、ReDoc 和 OpenAPI JSON 端点。"""
        app = create_app(settings=_make_test_settings(env=AppEnv.PRODUCTION))
        assert app.docs_url is None, "生产环境应禁用 Swagger UI"
        assert app.redoc_url is None, "生产环境应禁用 ReDoc"
        assert app.openapi_url is None, "生产环境应禁用 OpenAPI JSON 端点"

    def test_production_docs_endpoints_return_404(self) -> None:
        """生产环境下 /docs、/redoc、/openapi.json 端点返回 404。"""
        app = create_app(settings=_make_test_settings(env=AppEnv.PRODUCTION))
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/redoc").status_code == 404
            assert client.get("/openapi.json").status_code == 404

    def test_development_docs_endpoints_accessible(self) -> None:
        """开发环境下 /docs 和 /openapi.json 可访问。"""
        app = create_app(settings=_make_test_settings(env=AppEnv.DEVELOPMENT))
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200


# ===========================================================================
# 验收条件 5：文档不暴露内部密钥、数据库信息或堆栈（SPEC §9.6）
# ===========================================================================


class TestNoSensitiveInformation:
    """验证 OpenAPI 文档不暴露敏感信息（SPEC §9.6、§23.3）。"""

    def test_schema_text_has_no_sensitive_patterns(self, openapi_schema: dict[str, Any]) -> None:
        """OpenAPI JSON 序列化后不含敏感关键词。"""
        schema_text = json.dumps(openapi_schema, ensure_ascii=False).lower()
        for pattern in _SENSITIVE_PATTERNS:
            assert pattern not in schema_text, f"OpenAPI 文档暴露敏感关键词: '{pattern}'"

    def test_schema_has_no_server_urls_with_credentials(
        self,
        openapi_schema: dict[str, Any],
    ) -> None:
        """OpenAPI servers 不含数据库连接字符串或凭据。"""
        servers = openapi_schema.get("servers", [])
        for server in servers:
            url = str(server.get("url", "")).lower()
            for pattern in ("postgresql", "psycopg", "://", "password"):
                assert pattern not in url, f"server URL 含可疑信息: {url}"

    def test_schema_info_has_no_internal_details(self, openapi_schema: dict[str, Any]) -> None:
        """info 部分只含应用名称、版本和摘要，不含内部实现细节。"""
        info = openapi_schema.get("info", {})
        # 应包含 title、version
        assert "title" in info
        assert "version" in info
        # 不应包含内部细节字段
        for forbidden_key in ("host", "port", "database", "internal", "private"):
            assert forbidden_key not in info, f"info 含内部信息字段: {forbidden_key}"


# ===========================================================================
# 验收条件 6：Bearer 认证方案集成到 OpenAPI 安全方案（SPEC §9.6、§12.1）
# ===========================================================================


class TestBearerSecurityScheme:
    """验证 Bearer 认证方案集成到 OpenAPI 安全方案。"""

    def test_security_scheme_exists(self, openapi_schema: dict[str, Any]) -> None:
        """components.securitySchemes 包含 BearerAuth 方案。"""
        schemes = openapi_schema.get("components", {}).get("securitySchemes", {})
        assert SECURITY_SCHEME_NAME in schemes, f"安全方案缺少 {SECURITY_SCHEME_NAME}"

    def test_security_scheme_is_http_bearer(self, openapi_schema: dict[str, Any]) -> None:
        """BearerAuth 方案类型为 HTTP Bearer。"""
        scheme = (
            openapi_schema.get("components", {})
            .get("securitySchemes", {})
            .get(SECURITY_SCHEME_NAME, {})
        )
        assert scheme["type"] == "http", "安全方案类型应为 http"
        assert scheme["scheme"] == "bearer", "安全方案 scheme 应为 bearer"

    def test_security_scheme_has_description(self, openapi_schema: dict[str, Any]) -> None:
        """BearerAuth 方案具有用途说明。"""
        scheme = (
            openapi_schema.get("components", {})
            .get("securitySchemes", {})
            .get(SECURITY_SCHEME_NAME, {})
        )
        description = scheme.get("description")
        assert description is not None, "安全方案缺少 description"
        assert len(description) > 0, "安全方案 description 为空"

    def test_security_scheme_bearer_format(self, openapi_schema: dict[str, Any]) -> None:
        """BearerAuth 方案声明 bearerFormat。"""
        scheme = (
            openapi_schema.get("components", {})
            .get("securitySchemes", {})
            .get(SECURITY_SCHEME_NAME, {})
        )
        assert "bearerFormat" in scheme, "安全方案缺少 bearerFormat"
