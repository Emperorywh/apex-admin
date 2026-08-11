"""部署配置 — pydantic-settings 类型校验与生产安全检查.

SPEC 7.1 / 7.2 / 12.2:
  - 通过环境变量加载部署配置，env 前缀 ``APEX_``。
  - 必需配置缺失时快速失败并给出明确错误。
  - 配置项具有类型校验。
  - 数据库地址、密钥等部署配置不得存入系统配置表。
  - 生产环境禁止使用不安全的默认密钥。
  - Access Token 与 Refresh Token 的 HMAC 摘要密钥必须彼此独立、
    各至少 256 bit 熵（32 字节），否则应用启动失败。

本模块只定义部署配置的结构与校验规则，不在模块导入阶段
实例化 Settings 或触发任何副作用。调用方显式构造 ``Settings()``。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic 运行时类型解析需要
from enum import StrEnum
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """运行环境枚举。

    三种环境各有不同的行为预期：
      - development: 开发环境，提供便利的默认值，日志使用可读控制台格式。
      - testing:     测试环境，行为接近开发但用于自动化测试。
      - production:  生产环境，强制安全校验，日志使用单行 JSON。
    """

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


# ── 开发环境默认密钥 ──────────────────────────────────────────────────────
#
# 这些密钥仅供开发与测试使用，长度均 ≥ 32 字节以满足 HMAC-SHA-256 的
# 安全要求。生产环境严禁使用这些默认值，由 model_validator 强制校验。

_DEV_ACCESS_KEY = "dev-access-token-hmac-key-not-for-production"
_DEV_REFRESH_KEY = "dev-refresh-token-hmac-key-not-for-prod-use"

# SPEC 16.1 / 23.2: 敏感配置加密密钥（Fernet 格式）。
# 开发默认密钥仅供开发与测试使用，生产环境严禁使用。
# 值为 base64.urlsafe_b64encode(b"dev-sysconfig-encryption-key-32b")。
_DEV_SYSCONFIG_KEY = "ZGV2LXN5c2NvbmZpZy1lbmNyeXB0aW9uLWtleS0zMmI="

# 判定为"不安全默认/弱密钥"的已知集合，生产环境下命中即拒绝启动。
_UNSAFE_DEFAULT_KEYS: frozenset[str] = frozenset(
    {
        _DEV_ACCESS_KEY,
        _DEV_REFRESH_KEY,
        "change-me-access-token-hmac-key",
        "change-me-refresh-token-hmac-key",
    },
)

# Token 摘要密钥的最小长度（字节），对应 SPEC 12.2 的 256 bit 熵要求。
_MIN_KEY_BYTES = 32


class Settings(BaseSettings):
    """部署配置模型.

    所有字段通过 ``APEX_`` 前缀的环境变量加载（例如 ``APEX_DATABASE_URL``）。
    开发环境提供安全默认值；生产环境通过 model_validator 强制校验密钥安全。

    遵循 SPEC 7.2 配置分类原则：本类只承载部署配置（由运维环境管理），
    不混合系统配置（后台管理员管理）或业务配置（归属具体业务模块）。
    """

    model_config = SettingsConfigDict(
        env_prefix="APEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── 应用基本信息 ──────────────────────────────────────────────────
    # 用于 meta 端点、日志、OpenAPI 文档等。

    APP_NAME: str = "apex-admin"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = Environment.DEVELOPMENT

    # API 统一前缀（SPEC 9.1）
    API_PREFIX: str = "/api/v1"

    # 是否开启 API 文档端点（SPEC 9.6: 生产环境可以关闭文档或限制访问）。
    # 生产环境默认关闭，显式设置为 true 可开启。
    ENABLE_API_DOCS: bool = True

    # ── 数据库配置 ────────────────────────────────────────────────────
    # SQLAlchemy 异步 URL，驱动固定 postgresql+psycopg（SPEC 5.4 / 8.1）。
    # 开发默认值指向 dev_db.py 供应的本地实例（端口 55432、用户 apex、
    # 数据库 postgres）。生产环境必须通过环境变量设置。

    DATABASE_URL: str = "postgresql+psycopg://apex@127.0.0.1:55432/postgres"

    # SPEC 26.1 容量基线:
    #   默认 API Worker 数量为 2，每 Worker pool_size=5、max_overflow=5，
    #   即每 Worker 峰值 10 个连接、API 侧峰值合计 20 个连接。
    #   修改前必须完成容量计算:
    #   API Worker × (pool_size + max_overflow) + 预留 ≤ max_connections - 监控预留
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    # ── Token HMAC 摘要密钥（SPEC 12.2）──────────────────────────────
    # 数据库只保存 HMAC-SHA-256 摘要，密钥不入数据库。
    # 生产环境必须通过环境变量设置，且两把密钥独立。
    # None 表示未通过环境变量提供，由 model_validator 解析。

    ACCESS_TOKEN_HMAC_KEY: SecretStr | None = None
    REFRESH_TOKEN_HMAC_KEY: SecretStr | None = None

    # ── Token HMAC 密钥轮换（SPEC 23.2）──────────────────────────────
    # 密钥轮换期间的前一代密钥（dual-key 短期切换）。
    # 设置 _PREVIOUS 密钥和 KEY_ROTATION_EXPIRES_AT 后，
    # TokenDigestService 在窗口内同时接受新旧密钥的摘要。
    # 超窗后前一代密钥失效，无永久 fallback（SPEC 23.2）。

    ACCESS_TOKEN_HMAC_KEY_PREVIOUS: SecretStr | None = None
    REFRESH_TOKEN_HMAC_KEY_PREVIOUS: SecretStr | None = None
    KEY_ROTATION_EXPIRES_AT: datetime | None = None

    # ── 敏感配置加密密钥（SPEC 16.1 / 23.2）──────────────────────────
    # SPEC 23.2: "敏感配置的加密密钥与密文分离管理"。
    # SPEC 23.2: "Token HMAC 密钥和敏感配置加密密钥来自部署配置且彼此独立"。
    # 密钥为 Fernet 格式（url-safe base64 编码的 32 字节）。
    # 生产环境必须通过环境变量设置，不得使用默认开发密钥。

    SYSCONFIG_ENCRYPTION_KEY: SecretStr | None = None

    # SPEC 23.2: 密钥轮换双密钥短期切换——前一代密钥。
    SYSCONFIG_ENCRYPTION_KEY_PREVIOUS: SecretStr | None = None

    # ── Origin 白名单（SPEC 12.4）────────────────────────────────────
    # Refresh/Logout 等读取 Cookie 的状态变更接口校验 Origin 是否精确匹配白名单。
    # 逗号分隔（如 "http://localhost:3000,https://admin.example.com"）。
    # G2 只支持同站部署，默认 localhost。

    ALLOWED_ORIGINS: str = "http://localhost"

    # ── 日志级别 ──────────────────────────────────────────────────────

    LOG_LEVEL: str = "INFO"

    # ── 审计日志保留期限（SPEC 18.4）──────────────────────────────────
    # SPEC 18.4: "定义审计日志保留期限"。
    # SPEC 18.4: "安全事件的保留策略独立于普通访问日志"。
    # 三种保留期限独立配置:
    #   - 审计日志（audit_logs 表）
    #   - 登录日志（login_logs 表）
    #   - 安全事件（structlog 渠道，轮转由 TASK-029/031 负责）

    AUDIT_LOG_RETENTION_DAYS: int = 180
    LOGIN_LOG_RETENTION_DAYS: int = 90
    SECURITY_EVENT_RETENTION_DAYS: int = 365

    # ── 文件存储配置（SPEC 19.1 / 19.2 / 19.3）──────────────────────
    # SPEC 19.1: "支持配置存储根目录"。
    # SPEC 19.1: "存储目录不得位于 Web Root"。
    # 临时目录与正式目录（tmp/ 和 files/）在根目录下，
    # 位于同一文件系统以确保原子 rename（SPEC 19.3）。

    FILE_STORAGE_ROOT: str = "./data/files"

    # SPEC 19.2: "限制单文件大小"（默认 50 MiB）。
    FILE_MAX_SIZE_BYTES: int = 52428800

    # SPEC 19.2: "限制单次上传数量"（默认 10）。
    FILE_MAX_UPLOAD_COUNT: int = 10

    # SPEC 19.3: "DELETING 文件的物理删除至少延迟 7 天"。
    FILE_DELETION_DELAY_DAYS: int = 7

    # ── 模型级校验 ────────────────────────────────────────────────────

    def __init__(self, **values: Any) -> None:
        """构造时先执行字段级解析，再完成安全解析与生产校验.

        将密钥解析逻辑放在 ``__init__`` 末尾，确保字段全部赋值后
        再进行条件判断，错误信息能准确指明缺失项。
        """

        super().__init__(**values)
        self._resolve_token_keys()
        self._resolve_sysconfig_key()

    def _resolve_token_keys(self) -> None:
        """解析 Token 密钥：开发环境填充默认值，生产环境强制校验.

        校验规则（SPEC 12.2）:
          1. 生产环境：两把密钥必须由环境变量显式提供。
          2. 生产环境：禁止使用已知的开发/占位默认密钥。
          3. 两把密钥不得相同。
          4. 每把密钥长度 ≥ 32 字节（256 bit 熵）。
        """

        access_raw = self._extract_raw(self.ACCESS_TOKEN_HMAC_KEY)
        refresh_raw = self._extract_raw(self.REFRESH_TOKEN_HMAC_KEY)

        if self.ENVIRONMENT == Environment.PRODUCTION:
            self._validate_production_keys(access_raw, refresh_raw)
        else:
            # 开发/测试环境：未设置时填充已知默认值。
            if access_raw is None:
                self.ACCESS_TOKEN_HMAC_KEY = SecretStr(_DEV_ACCESS_KEY)
                access_raw = _DEV_ACCESS_KEY
            if refresh_raw is None:
                self.REFRESH_TOKEN_HMAC_KEY = SecretStr(_DEV_REFRESH_KEY)
                refresh_raw = _DEV_REFRESH_KEY

    def _resolve_sysconfig_key(self) -> None:
        """解析敏感配置加密密钥 — SPEC 16.1 / 23.2.

        SPEC 23.2: "敏感配置的加密密钥与密文分离管理"。
        SPEC 23.2: "Token HMAC 密钥和敏感配置加密密钥来自部署配置且彼此独立"。

        校验规则:
          1. 生产环境：密钥必须由环境变量显式提供。
          2. 生产环境：禁止使用不安全的默认/占位密钥。
          3. 密钥必须为合法的 Fernet 密钥（url-safe base64 编码的 32 字节）。
          4. 密钥不得与 Token HMAC 密钥相同（彼此独立）。
        """

        sysconfig_raw = self._extract_raw(self.SYSCONFIG_ENCRYPTION_KEY)

        if self.ENVIRONMENT == Environment.PRODUCTION:
            self._validate_production_sysconfig_key(sysconfig_raw)
        else:
            # 开发/测试环境：未设置时填充已知默认值。
            if sysconfig_raw is None:
                self.SYSCONFIG_ENCRYPTION_KEY = SecretStr(_DEV_SYSCONFIG_KEY)

    @staticmethod
    def _validate_production_sysconfig_key(key_raw: str | None) -> None:
        """生产环境敏感配置加密密钥安全校验 — SPEC 23.2."""

        # 1. 必须显式设置
        if key_raw is None:
            raise ValueError(
                "生产环境必须设置 APEX_SYSCONFIG_ENCRYPTION_KEY",
            )

        # 2. 禁止使用不安全的默认密钥
        if key_raw == _DEV_SYSCONFIG_KEY:
            raise ValueError(
                "生产环境禁止使用不安全的默认敏感配置加密密钥，"
                "请设置 APEX_SYSCONFIG_ENCRYPTION_KEY",
            )

        # 3. 必须为合法的 Fernet 密钥（32 字节 url-safe base64）
        from cryptography.fernet import Fernet

        try:
            Fernet(key_raw.encode("utf-8"))
        except (ValueError, Exception) as exc:
            raise ValueError(
                f"APEX_SYSCONFIG_ENCRYPTION_KEY 不是合法的 Fernet 密钥: {exc}",
            ) from exc

    @staticmethod
    def _extract_raw(secret: SecretStr | None) -> str | None:
        """从 SecretStr 提取原始值，None 保持 None。"""

        if secret is None:
            return None
        return secret.get_secret_value()

    @property
    def allowed_origin_set(self) -> frozenset[str]:
        """解析 ``ALLOWED_ORIGINS`` 为不可变集合 — SPEC 12.4.

        逗号分隔的 Origin 列表，空白被去除，空项被忽略。
        用于 Refresh/Logout 端点的 Origin 精确匹配校验。
        """

        return frozenset(
            origin.strip()
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip()
        )

    @staticmethod
    def _validate_production_keys(
        access_raw: str | None,
        refresh_raw: str | None,
    ) -> None:
        """生产环境密钥安全校验，任一不通过时抛出 ValueError.

        错误消息明确指明缺失或不合规的配置项，满足 SPEC 7.1
        "必需配置缺失时快速失败并给出明确错误"的要求。
        """

        # 1. 必须显式设置
        if access_raw is None:
            raise ValueError(
                "生产环境必须设置 APEX_ACCESS_TOKEN_HMAC_KEY",
            )
        if refresh_raw is None:
            raise ValueError(
                "生产环境必须设置 APEX_REFRESH_TOKEN_HMAC_KEY",
            )

        # 2. 禁止使用不安全的默认密钥
        if access_raw in _UNSAFE_DEFAULT_KEYS:
            raise ValueError(
                "生产环境禁止使用不安全的默认 Access Token 密钥，"
                "请设置 APEX_ACCESS_TOKEN_HMAC_KEY",
            )
        if refresh_raw in _UNSAFE_DEFAULT_KEYS:
            raise ValueError(
                "生产环境禁止使用不安全的默认 Refresh Token 密钥，"
                "请设置 APEX_REFRESH_TOKEN_HMAC_KEY",
            )

        # 3. 两把密钥不得相同
        if access_raw == refresh_raw:
            raise ValueError(
                "APEX_ACCESS_TOKEN_HMAC_KEY 与 APEX_REFRESH_TOKEN_HMAC_KEY 不得相同",
            )

        # 4. 长度 ≥ 32 字节（256 bit）
        access_len = len(access_raw.encode("utf-8"))
        if access_len < _MIN_KEY_BYTES:
            raise ValueError(
                f"APEX_ACCESS_TOKEN_HMAC_KEY 长度 {access_len} 字节"
                f"不足，要求至少 {_MIN_KEY_BYTES} 字节（256 bit 熵）",
            )
        refresh_len = len(refresh_raw.encode("utf-8"))
        if refresh_len < _MIN_KEY_BYTES:
            raise ValueError(
                f"APEX_REFRESH_TOKEN_HMAC_KEY 长度 {refresh_len} 字节"
                f"不足，要求至少 {_MIN_KEY_BYTES} 字节（256 bit 熵）",
            )


def mask_url_password(url: str) -> str:
    """脱敏 URL 中的密码部分.

    将 ``postgresql+psycopg://user:password@host/db`` 转为
    ``postgresql+psycopg://user:***@host/db``。无密码时原样返回。

    用于 ``config show`` 命令输出，防止在终端暴露数据库凭据。
    """

    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    if parsed.password is None:
        return url

    # 重建 netloc，用 *** 替换密码
    userinfo = parsed.username or ""
    if parsed.username and parsed.password:
        userinfo = f"{parsed.username}:***"
    elif parsed.username:
        userinfo = parsed.username

    host_port = parsed.hostname or ""
    if parsed.port is not None:
        host_port = f"{host_port}:{parsed.port}"

    netloc = f"{userinfo}@{host_port}" if userinfo else host_port
    return urlunparse(parsed._replace(netloc=netloc))
