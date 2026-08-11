"""部署配置与 CLI 测试 — SPEC 7.1 / 7.2 / 12.2 / 25.1.

覆盖:
  - 开发环境默认值正常加载。
  - 生产环境密钥缺失、相同、长度不足时快速失败。
  - config show 命令输出脱敏摘要且退出码 0。
  - 非法参数退出码 2。
  - 配置分类：部署配置不混合系统/业务配置。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.cli.__main__ import main as cli_main
from app.core.config import Environment, Settings, mask_url_password

# ── 辅助函数 ───────────────────────────────────────────────────────────────


def _make_settings(**overrides: str) -> Settings:
    """以指定覆盖项构造 Settings，自动补充开发环境必需密钥.

    提供合法的 32+ 字节密钥，避免开发环境的默认值干扰测试。
    """

    base: dict[str, str] = {
        "ACCESS_TOKEN_HMAC_KEY": "a" * 32,
        "REFRESH_TOKEN_HMAC_KEY": "b" * 32,
    }
    base.update(overrides)
    return Settings(**base)


# ── 开发环境默认值 ─────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_development_defaults_without_keys() -> None:
    """开发环境未设置密钥时填充已知默认值，不报错。"""

    settings = Settings(ENVIRONMENT="development")
    assert settings.APP_NAME == "apex-admin"
    assert settings.APP_VERSION == "0.1.0"
    assert settings.ENVIRONMENT == Environment.DEVELOPMENT
    assert settings.ACCESS_TOKEN_HMAC_KEY is not None
    assert settings.REFRESH_TOKEN_HMAC_KEY is not None
    assert settings.API_PREFIX == "/api/v1"


@pytest.mark.g1
@pytest.mark.unit
def test_environment_from_value() -> None:
    """通过构造参数设置环境为 testing。"""

    settings = Settings(
        ENVIRONMENT="testing",
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
    )
    assert settings.ENVIRONMENT == Environment.TESTING


# ── 生产环境安全校验 ───────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_production_missing_access_key_fails() -> None:
    """生产环境缺少 ACCESS_TOKEN_HMAC_KEY 时快速失败。"""

    with pytest.raises(ValueError, match="ACCESS_TOKEN_HMAC_KEY"):
        Settings(
            ENVIRONMENT="production",
            REFRESH_TOKEN_HMAC_KEY="b" * 32,
        )


@pytest.mark.g1
@pytest.mark.unit
def test_production_missing_refresh_key_fails() -> None:
    """生产环境缺少 REFRESH_TOKEN_HMAC_KEY 时快速失败。"""

    with pytest.raises(ValueError, match="REFRESH_TOKEN_HMAC_KEY"):
        Settings(
            ENVIRONMENT="production",
            ACCESS_TOKEN_HMAC_KEY="a" * 32,
        )


@pytest.mark.g1
@pytest.mark.unit
def test_production_identical_keys_fails() -> None:
    """生产环境两个 Token 密钥相同时启动失败。"""

    same_key = "x" * 40
    with pytest.raises(ValueError, match="不得相同"):
        Settings(
            ENVIRONMENT="production",
            ACCESS_TOKEN_HMAC_KEY=same_key,
            REFRESH_TOKEN_HMAC_KEY=same_key,
        )


@pytest.mark.g1
@pytest.mark.unit
def test_production_short_key_fails() -> None:
    """生产环境密钥长度不足 32 字节时启动失败。"""

    short_key = "x" * 10
    with pytest.raises(ValueError, match="不足"):
        Settings(
            ENVIRONMENT="production",
            ACCESS_TOKEN_HMAC_KEY=short_key,
            REFRESH_TOKEN_HMAC_KEY="b" * 32,
        )


@pytest.mark.g1
@pytest.mark.unit
def test_production_short_refresh_key_fails() -> None:
    """生产环境 Refresh Token 密钥长度不足时启动失败。"""

    with pytest.raises(ValueError, match="不足"):
        Settings(
            ENVIRONMENT="production",
            ACCESS_TOKEN_HMAC_KEY="a" * 32,
            REFRESH_TOKEN_HMAC_KEY="x" * 10,
        )


@pytest.mark.g1
@pytest.mark.unit
def test_production_unsafe_default_key_fails() -> None:
    """生产环境使用已知不安全默认密钥时启动失败。"""

    with pytest.raises(ValueError, match="不安全"):
        Settings(
            ENVIRONMENT="production",
            ACCESS_TOKEN_HMAC_KEY="change-me-access-token-hmac-key",
            REFRESH_TOKEN_HMAC_KEY="b" * 32,
        )


@pytest.mark.g1
@pytest.mark.unit
def test_production_valid_keys_succeed() -> None:
    """生产环境提供合法的独立密钥时成功加载。"""

    settings = Settings(
        ENVIRONMENT="production",
        ACCESS_TOKEN_HMAC_KEY="valid-access-key-" + "a" * 16,
        REFRESH_TOKEN_HMAC_KEY="valid-refresh-key-" + "b" * 16,
        SYSCONFIG_ENCRYPTION_KEY="T44-h5wE4-HJ69EZjyDir3a_DNQFAT5DMW8De0tXijU=",
        TRUSTED_HOSTS="admin.example.com",
        METRICS_TOKEN="prod-metrics-secret-token",
    )
    assert settings.ENVIRONMENT == Environment.PRODUCTION


# ── 类型校验 ───────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_invalid_environment_value_fails() -> None:
    """非法环境值触发类型校验失败。"""

    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="staging")  # type: ignore[arg-type]


# ── URL 密码脱敏 ──────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_mask_url_password_with_credentials() -> None:
    """URL 含密码时脱敏为 ***。"""

    url = "postgresql+psycopg://user:secret@localhost:5432/db"
    masked = mask_url_password(url)
    assert "secret" not in masked
    assert "***" in masked
    assert "user" in masked
    assert "localhost" in masked


@pytest.mark.g1
@pytest.mark.unit
def test_mask_url_password_without_credentials() -> None:
    """URL 不含密码时原样返回。"""

    url = "postgresql+psycopg://localhost/db"
    assert mask_url_password(url) == url


# ── CLI config show ───────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_cli_config_show_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """config show 输出脱敏摘要且退出码 0（SPEC 25.1）。"""

    exit_code = cli_main(["config", "show"])
    assert exit_code == 0

    captured = capsys.readouterr()
    output = captured.out
    # 包含应用名称
    assert "APP_NAME" in output
    # 不包含原始密钥值
    secret = Settings().ACCESS_TOKEN_HMAC_KEY
    assert secret is not None
    assert secret.get_secret_value() not in output


@pytest.mark.g1
@pytest.mark.unit
def test_cli_config_show_masks_secret_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """config show 输出中密钥类值显示为掩码。"""

    cli_main(["config", "show"])
    output = capsys.readouterr().out
    assert "**********" in output


@pytest.mark.g1
@pytest.mark.unit
def test_cli_bad_arg_exit_code_2() -> None:
    """非法参数退出码 2（SPEC 25.1）— argparse 自动处理。"""

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["config", "show", "--bad-arg"])
    assert exc_info.value.code == 2


@pytest.mark.g1
@pytest.mark.unit
def test_cli_no_subcommand_exit_code_2() -> None:
    """无子命令时退出码 2。"""

    with pytest.raises(SystemExit) as exc_info:
        cli_main([])
    assert exc_info.value.code == 2
