"""部署配置单元测试（SPEC §7.1–7.2）。

覆盖验收条件：
- Settings 类从环境变量加载并具有类型校验
- 必需配置缺失时快速失败
- 所有配置字段具有清晰名称、默认值和简体中文用途说明
- 生产环境拒绝不安全默认密钥
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.config.settings import AppEnv, Settings

pytestmark = [pytest.mark.unit, pytest.mark.g1]

# 测试用的有效密钥（64 位 hex = 32 字节，字节值多样，非退化密钥）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"

# 测试中涉及的配置环境变量名
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


def _make_valid_kwargs(**overrides: Any) -> dict[str, Any]:
    """生成一组有效的配置字段，可通过 overrides 覆盖。"""
    defaults: dict[str, Any] = {
        "app_env": AppEnv.DEVELOPMENT,
        "database_url": "postgresql+psycopg://apex:secret@localhost:5432/apex_admin",
        "access_token_hmac_key": _VALID_ACCESS_KEY,
        "refresh_token_hmac_key": _VALID_REFRESH_KEY,
        "config_encryption_key": _VALID_ENCRYPTION_KEY,
        "file_storage_root": "/data/files",
    }
    defaults.update(overrides)
    return defaults


def _build_settings(**kwargs: Any) -> Settings:
    """构造 Settings 实例，禁用 .env 文件加载以确保测试隔离。"""
    return Settings(_env_file=None, **kwargs)


# ---------------------------------------------------------------------------
# 有效配置加载
# ---------------------------------------------------------------------------


class TestSettingsValid:
    """验证有效配置能正确加载并具有类型。"""

    def test_loads_all_fields(self) -> None:
        """全部字段提供有效值时成功构造。"""
        settings = _build_settings(**_make_valid_kwargs())
        assert settings.app_env == AppEnv.DEVELOPMENT
        assert settings.database_url == "postgresql+psycopg://apex:secret@localhost:5432/apex_admin"
        assert settings.file_storage_root == "/data/files"

    def test_secret_str_does_not_expose_plaintext(self) -> None:
        """密钥字段使用 SecretStr，repr 不泄露明文。"""
        settings = _build_settings(**_make_valid_kwargs())
        assert "1a2b3c" not in repr(settings.access_token_hmac_key)
        assert "**********" in repr(settings.access_token_hmac_key)

    def test_allowed_origins_default(self) -> None:
        """未显式设置时 allowed_origins 使用开发默认值。"""
        settings = _build_settings(**_make_valid_kwargs())
        assert settings.allowed_origins == ["http://localhost:3000"]

    def test_allowed_origins_custom(self) -> None:
        """显式设置时 allowed_origins 使用自定义值。"""
        settings = _build_settings(
            **_make_valid_kwargs(allowed_origins=["https://example.com", "https://app.example.com"])
        )
        assert settings.allowed_origins == ["https://example.com", "https://app.example.com"]


# ---------------------------------------------------------------------------
# 必需配置缺失快速失败（验收条件 1）
# ---------------------------------------------------------------------------


class TestRequiredFields:
    """验证必需配置缺失时快速失败。"""

    @pytest.mark.parametrize(
        "missing_field",
        [
            "app_env",
            "database_url",
            "access_token_hmac_key",
            "refresh_token_hmac_key",
            "config_encryption_key",
            "file_storage_root",
        ],
    )
    def test_missing_required_field_raises(self, missing_field: str) -> None:
        """缺少任一必需字段时抛出 ValidationError。"""
        kwargs = _make_valid_kwargs()
        del kwargs[missing_field]
        with pytest.raises(ValidationError):
            _build_settings(**kwargs)

    def test_empty_string_key_raises(self) -> None:
        """密钥为空字符串时因熵不足而失败。"""
        with pytest.raises(ValidationError, match="熵不足"):
            _build_settings(**_make_valid_kwargs(access_token_hmac_key=""))


# ---------------------------------------------------------------------------
# 类型校验（验收条件 0）
# ---------------------------------------------------------------------------


class TestTypeValidation:
    """验证配置项类型校验。"""

    def test_invalid_app_env_raises(self) -> None:
        """无效的运行环境值抛出 ValidationError。"""
        with pytest.raises(ValidationError):
            _build_settings(**_make_valid_kwargs(app_env="staging"))


# ---------------------------------------------------------------------------
# 密钥安全校验（SPEC §12.2, §23.2）
# ---------------------------------------------------------------------------


class TestKeySecurity:
    """验证 Token HMAC 密钥和加密密钥的安全规则。"""

    def test_same_hmac_keys_raises(self) -> None:
        """Access 与 Refresh Token 密钥相同时失败。"""
        key = _VALID_ACCESS_KEY
        with pytest.raises(ValidationError, match="不得相同"):
            _build_settings(
                **_make_valid_kwargs(access_token_hmac_key=key, refresh_token_hmac_key=key)
            )

    def test_short_key_raises(self) -> None:
        """密钥长度不足 256 bit 时失败。"""
        with pytest.raises(ValidationError, match="熵不足"):
            _build_settings(**_make_valid_kwargs(access_token_hmac_key="abc"))

    def test_encryption_key_same_as_access_raises(self) -> None:
        """加密密钥与 Access Token 密钥相同时失败。"""
        key = _VALID_ACCESS_KEY
        with pytest.raises(ValidationError, match="彼此独立"):
            _build_settings(
                **_make_valid_kwargs(access_token_hmac_key=key, config_encryption_key=key)
            )

    def test_encryption_key_same_as_refresh_raises(self) -> None:
        """加密密钥与 Refresh Token 密钥相同时失败。"""
        key = _VALID_REFRESH_KEY
        with pytest.raises(ValidationError, match="彼此独立"):
            _build_settings(
                **_make_valid_kwargs(refresh_token_hmac_key=key, config_encryption_key=key)
            )


# ---------------------------------------------------------------------------
# 生产环境拒绝不安全默认密钥（验收条件 3）
# ---------------------------------------------------------------------------


class TestProductionSecurity:
    """验证生产环境安全检查。"""

    def test_production_rejects_degenerate_key(self) -> None:
        """生产环境拒绝全零退化密钥（通过熵检查但为弱密钥）。"""
        degenerate_key = "00" * 32
        with pytest.raises(ValidationError, match="不安全"):
            _build_settings(
                **_make_valid_kwargs(
                    app_env=AppEnv.PRODUCTION,
                    access_token_hmac_key=degenerate_key,
                    allowed_origins=["https://example.com"],
                )
            )

    def test_production_rejects_dev_cors(self) -> None:
        """生产环境拒绝开发默认 CORS 来源。"""
        with pytest.raises(ValidationError, match="CORS"):
            _build_settings(**_make_valid_kwargs(app_env=AppEnv.PRODUCTION))

    def test_production_rejects_empty_cors(self) -> None:
        """生产环境拒绝空 CORS 列表。"""
        with pytest.raises(ValidationError, match="CORS"):
            _build_settings(**_make_valid_kwargs(app_env=AppEnv.PRODUCTION, allowed_origins=[]))

    def test_production_valid_succeeds(self) -> None:
        """生产环境提供全部有效值时成功构造。"""
        settings = _build_settings(
            **_make_valid_kwargs(
                app_env=AppEnv.PRODUCTION,
                allowed_origins=["https://example.com"],
            )
        )
        assert settings.app_env == AppEnv.PRODUCTION

    def test_development_allows_degenerate_key(self) -> None:
        """开发环境允许通过熵检查但模式不安全的密钥（降低本地开发门槛）。"""
        degenerate_key = "00" * 32
        settings = _build_settings(**_make_valid_kwargs(access_token_hmac_key=degenerate_key))
        assert settings.access_token_hmac_key.get_secret_value() == degenerate_key


# ---------------------------------------------------------------------------
# 脱敏配置摘要（SPEC §25.1）
# ---------------------------------------------------------------------------


class TestSafeSummary:
    """验证 config show 命令的脱敏输出。"""

    def test_masks_all_secret_fields(self) -> None:
        """摘要中所有密钥字段显示为 ***。"""
        settings = _build_settings(**_make_valid_kwargs())
        summary = settings.to_safe_summary()
        assert summary["access_token_hmac_key"] == "***"
        assert summary["refresh_token_hmac_key"] == "***"
        assert summary["config_encryption_key"] == "***"

    def test_masks_database_url_credentials(self) -> None:
        """摘要中数据库 URL 的凭据部分被隐藏。"""
        settings = _build_settings(**_make_valid_kwargs())
        summary = settings.to_safe_summary()
        url = summary["database_url"]
        assert isinstance(url, str)
        assert "secret" not in url
        assert "***" in url
        assert "localhost:5432/apex_admin" in url

    def test_shows_non_sensitive_fields(self) -> None:
        """摘要正确展示非敏感字段。"""
        settings = _build_settings(**_make_valid_kwargs())
        summary = settings.to_safe_summary()
        assert summary["app_env"] == "development"
        assert summary["file_storage_root"] == "/data/files"
        assert summary["allowed_origins"] == ["http://localhost:3000"]
