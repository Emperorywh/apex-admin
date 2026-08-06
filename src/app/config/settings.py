"""部署配置（SPEC §7.1）。

通过 pydantic-settings 从环境变量加载部署配置，提供类型校验、必需配置快速失败、
Token HMAC 密钥安全校验和生产环境默认密钥拒绝。

部署配置由运维环境管理，与系统配置（§16，后台管理员管理）和业务配置（§7.2，
业务模块管理）严格分离。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Token HMAC 密钥最小熵：256 bit = 32 字节（SPEC §12.2）
_MIN_KEY_BYTES: int = 32

# 已知不安全占位密钥，生产环境禁止使用
_UNSAFE_PLACEHOLDER_KEYS: frozenset[str] = frozenset(
    {
        "changeme",
        "secret",
        "password",
        "test-key",
        "dev-key",
        "placeholder",
        "replace-me",
    }
)


class AppEnv(StrEnum):
    """运行环境类型（SPEC §6.1）。

    - DEVELOPMENT：本地开发和调试
    - TESTING：自动化测试
    - PRODUCTION：生产部署
    """

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """部署配置（SPEC §7.1）。

    所有字段从环境变量加载。必需配置缺失时快速失败并给出明确错误。
    Token HMAC 密钥与敏感配置加密密钥彼此独立（SPEC §23.2）。

    字段对应的环境变量名默认与字段名大写形式一致，例如 ``database_url``
    对应 ``DATABASE_URL``。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 运行环境（SPEC §6.1）----
    app_env: AppEnv = Field(
        description="运行环境（development / testing / production），决定安全检查严格程度",
    )

    # ---- 数据库（SPEC §8.1）----
    database_url: str = Field(
        description=(
            "PostgreSQL 连接 URL，格式 "
            "postgresql+psycopg://<user>:<password>@<host>:<port>/<dbname>"
        ),
    )

    # ---- Token HMAC 密钥（SPEC §12.2）----
    access_token_hmac_key: SecretStr = Field(
        description=(
            "Access Token HMAC-SHA-256 摘要密钥，至少 256 bit 熵（32 字节），"
            "建议使用 openssl rand -hex 32 生成"
        ),
    )
    refresh_token_hmac_key: SecretStr = Field(
        description=(
            "Refresh Token HMAC-SHA-256 摘要密钥，至少 256 bit 熵，必须与 Access Token 密钥不同"
        ),
    )

    # ---- 敏感配置加密（SPEC §23.2）----
    config_encryption_key: SecretStr = Field(
        description="系统配置中敏感字段的加密密钥，必须与 Token HMAC 密钥彼此独立",
    )

    # ---- 文件存储（SPEC §19.1）----
    file_storage_root: str = Field(
        description="本地文件存储根目录路径，不得位于 Web Root",
    )

    # ---- CORS 允许来源（SPEC §23.1）----
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="CORS 允许来源白名单（JSON 数组格式），生产环境必须显式配置",
    )

    @field_validator("access_token_hmac_key", "refresh_token_hmac_key", "config_encryption_key")
    @classmethod
    def _validate_key_entropy(cls, v: SecretStr) -> SecretStr:
        """校验密钥熵不低于 256 bit（SPEC §12.2）。

        尝试 hex 解码后检查字节长度；非 hex 格式时以字符串长度作为保守估计。
        密钥缺失（空字符串）在必填校验阶段已被拦截，此处额外确保长度达标。
        """
        raw = v.get_secret_value()
        key_bytes = _decode_key_bytes(raw)
        if key_bytes < _MIN_KEY_BYTES:
            raise ValueError(
                f"密钥熵不足：有效长度 {key_bytes} 字节，要求至少 {_MIN_KEY_BYTES} 字节（256 bit）"
            )
        return v

    @model_validator(mode="after")
    def _validate_security_constraints(self) -> Self:
        """跨字段安全校验。

        - Access 与 Refresh Token 密钥必须不同（SPEC §12.2）
        - 敏感配置加密密钥必须与 Token HMAC 密钥彼此独立（SPEC §23.2）
        - 生产环境拒绝已知不安全占位密钥和退化密钥（SPEC §7.1）
        - 生产环境必须显式配置 CORS 允许来源，禁止使用开发默认值（SPEC §23.1）
        """
        access = self.access_token_hmac_key.get_secret_value()
        refresh = self.refresh_token_hmac_key.get_secret_value()
        encryption = self.config_encryption_key.get_secret_value()

        # Access 与 Refresh Token 密钥必须不同
        if access == refresh:
            raise ValueError("Access Token 与 Refresh Token 的 HMAC 密钥不得相同（SPEC §12.2）")

        # 加密密钥必须与 Token 密钥彼此独立
        if encryption in (access, refresh):
            raise ValueError("敏感配置加密密钥必须与 Token HMAC 密钥彼此独立（SPEC §23.2）")

        # 生产环境额外安全检查
        if self.app_env == AppEnv.PRODUCTION:
            self._reject_unsafe_production_keys(access, refresh, encryption)
            self._reject_development_cors()

        return self

    def _reject_unsafe_production_keys(self, access: str, refresh: str, encryption: str) -> None:
        """生产环境拒绝已知不安全占位密钥和退化密钥模式。"""
        for field_name, value in (
            ("access_token_hmac_key", access),
            ("refresh_token_hmac_key", refresh),
            ("config_encryption_key", encryption),
        ):
            if _is_weak_key(value):
                raise ValueError(
                    f"生产环境禁止使用不安全密钥（字段 {field_name}），"
                    f"请使用 openssl rand -hex 32 生成独立密钥"
                )

    def _reject_development_cors(self) -> None:
        """生产环境必须显式配置 CORS 来源，禁止使用开发默认值。"""
        if not self.allowed_origins or self.allowed_origins == ["http://localhost:3000"]:
            raise ValueError(
                "生产环境必须显式配置 CORS 允许来源（ALLOWED_ORIGINS），"
                "禁止使用开发默认值（SPEC §23.1）"
            )

    def to_safe_summary(self) -> dict[str, str | list[str]]:
        """生成脱敏配置摘要，供 config show 命令使用（SPEC §25.1）。

        敏感字段以 ``***`` 掩码，数据库 URL 隐藏凭据部分，不泄露任何密钥明文。
        """
        return {
            "app_env": str(self.app_env.value),
            "database_url": _mask_url_credentials(self.database_url),
            "access_token_hmac_key": "***",
            "refresh_token_hmac_key": "***",
            "config_encryption_key": "***",
            "file_storage_root": self.file_storage_root,
            "allowed_origins": list(self.allowed_origins),
        }


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _decode_key_bytes(key: str) -> int:
    """计算密钥的有效字节长度。

    优先尝试 hex 解码；非 hex 格式时以字符串长度作为保守下界。
    """
    try:
        decoded = bytes.fromhex(key)
    except ValueError:
        return len(key)
    return len(decoded)


def _is_weak_key(key: str) -> bool:
    """判断密钥是否为已知的弱密钥模式。

    检查项：
    - 匹配已知不安全占位值（changeme、secret 等）
    - hex 解码后所有字节相同（全零、全 0xff 等退化密钥）
    """
    lowered = key.lower()
    if lowered in _UNSAFE_PLACEHOLDER_KEYS:
        return True
    try:
        decoded = bytes.fromhex(key)
    except ValueError:
        return False
    return len(set(decoded)) <= 1


def _mask_url_credentials(url: str) -> str:
    """脱敏 URL 中的凭据部分。

    将 ``scheme://user:password@host`` 转换为 ``scheme://***@host``。
    """
    scheme_sep = "://"
    scheme_end = url.find(scheme_sep)
    if scheme_end == -1:
        return url
    rest_start = scheme_end + len(scheme_sep)
    at_index = url.find("@", rest_start)
    if at_index == -1:
        return url
    return url[:rest_start] + "***" + url[at_index:]
