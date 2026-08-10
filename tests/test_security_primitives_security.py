"""密码与 Token 安全基元安全测试 — SPEC 23.2 / 24.1 / 12.4.

覆盖验收标准（g2 security）:
  - AC-4: 安全测试证明密码哈希与 Token 值不进入日志输出。

SPEC 23.2: "禁止记录和回显密码"。
SPEC 24.1: "过滤密码、Token、Cookie、密钥和其他敏感字段"。
SPEC 12.4: "不在日志中记录明文密码、完整 Token"。

不依赖数据库（security marker）。
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
import structlog

from app.core.config import Environment, Settings
from app.core.logging import configure_logging
from app.core.security.digest import TokenDigestService
from app.core.security.password import DUMMY_PASSWORD_HASH, Argon2Hasher
from app.core.security.token import generate_token


@pytest.fixture()
def test_logger(
    capsys: pytest.CaptureFixture[str],
) -> structlog.stdlib.BoundLogger:
    """配置 structlog 并返回一个 logger 实例，捕获输出。

    配置与生产环境一致的单行 JSON 输出，但使用 testing 环境的日志配置，
    确保 ``mask_sensitive_fields`` 处理器在管线中。
    """

    settings = Settings(
        ENVIRONMENT=Environment.TESTING,
        ACCESS_TOKEN_HMAC_KEY="a" * 32,
        REFRESH_TOKEN_HMAC_KEY="b" * 32,
    )
    configure_logging(settings)
    return structlog.get_logger("test_security")


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: 密码哈希不进入日志
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.security
class TestPasswordHashNotInLogs:
    """密码哈希值不进入日志输出 — SPEC 23.2 / 24.1."""

    def test_plain_password_not_in_log_output(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """明文密码不进入日志 — structlog 掩码处理器。"""

        plain_password = "super_secret_plain_password_12345"
        test_logger.info(
            "login_attempt",
            username="testuser",
            password=plain_password,
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert plain_password not in output

    def test_password_hash_value_not_in_log_output(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """密码哈希值不进入日志 — 含 password 的键被掩码。

        SPEC 23.2: "禁止记录和回显密码"。
        structlog 的 ``mask_sensitive_fields`` 处理器对键名包含
        password 片段的值执行掩码（SPEC 24.1）。
        """

        hasher = Argon2Hasher()
        password_hash = hasher.hash("my_secure_password_123")
        test_logger.info(
            "user_created",
            username="testuser",
            password_hash=password_hash,
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        # 哈希值（或其可辨识片段）不应出现在输出中
        assert password_hash not in output
        # PHC 格式头不应出现
        assert "$argon2id$" not in output

    def test_password_in_nested_dict_masked(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """嵌套字典中的密码字段被递归掩码。"""

        plain_password = "nested_secret_password_12345"
        test_logger.info(
            "event_with_nested_data",
            user_info={
                "name": "alice",
                "profile": {"password": plain_password},
            },
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert plain_password not in output

    def test_dummy_hash_not_logged_as_password(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """防枚举虚拟哈希不以明文形式出现在日志中。

        SPEC 12.4: 安全日志中不出现密码相关值。
        """

        test_logger.info(
            "anti_enumeration_check",
            dummy_password_hash=DUMMY_PASSWORD_HASH,
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert DUMMY_PASSWORD_HASH not in output


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: Token 值不进入日志
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.security
class TestTokenValueNotInLogs:
    """Token 值不进入日志输出 — SPEC 12.4 / 24.1."""

    def test_access_token_not_in_log_output(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Access Token 值不进入日志 — 含 token 的键被掩码。"""

        token = generate_token()
        test_logger.info(
            "token_issued",
            username="testuser",
            access_token=token,
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert token not in output

    def test_refresh_token_not_in_log_output(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Refresh Token 值不进入日志。"""

        token = generate_token()
        test_logger.info(
            "refresh_token_rotated",
            username="testuser",
            refresh_token=token,
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert token not in output

    def test_raw_token_not_in_log_output(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """原始 Token 值不进入日志 — 通用 token 键被掩码。"""

        token = generate_token()
        test_logger.info(
            "session_event",
            token=token,
            action="session_created",
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert token not in output

    def test_token_digest_not_in_log_output(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Token 摘要值不以 token 相关键名进入日志。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        token = generate_token()
        digest = service.digest_access_token(token)
        test_logger.info(
            "token_stored",
            access_token_digest=digest,
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        # digest 以 token 相关键名传递时被掩码
        assert digest not in output

    def test_bearer_prefix_not_in_log(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """含 Bearer 前缀的 Authorization 头不进入日志。"""

        token = generate_token()
        test_logger.info(
            "api_request",
            authorization=f"Bearer {token}",
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert token not in output
        assert f"Bearer {token}" not in output

    def test_hmac_keys_not_in_log(
        self,
        test_logger: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """HMAC 密钥不进入日志 — secret 键被掩码。"""

        access_key = "very_secret_access_key_value_12345"
        test_logger.info(
            "config_loaded",
            access_token_secret=access_key,
        )

        output = capsys.readouterr().out + capsys.readouterr().err
        assert access_key not in output


# ═══════════════════════════════════════════════════════════════════════════════
# 额外安全验证：摘要与明文不可逆推
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.security
class TestDigestIrreversibility:
    """Token 摘要与明文 Token 不可逆推 — SPEC 12.2 安全保障."""

    def test_digest_cannot_be_reversed_to_token(self) -> None:
        """HMAC-SHA-256 摘要不可逆推回原始 Token。

        确保即使数据库泄露，攻击者也无法从摘要逆推明文 Token。
        """

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        token = "sensitive_bearer_token_value_12345"
        digest = service.digest_access_token(token)

        # 摘要与 token 完全不同
        assert digest != token
        # 摘要不包含 token 的任何子串（长度 >= 5 的子串）
        for i in range(len(token) - 4):
            substr = token[i : i + 5]
            assert substr not in digest

    def test_digest_matches_reference_hmac(self) -> None:
        """摘要与标准库 HMAC-SHA-256 计算结果一致 — 无自定义后门。

        确保实现使用标准 HMAC-SHA-256，而非自定义弱哈希。
        """

        access_key = b"a" * 32
        service = TokenDigestService(access_key, b"b" * 32)
        token = "verify_hmac_token_value_12345"

        expected = hmac.new(
            access_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        assert service.digest_access_token(token) == expected

    def test_two_different_services_same_keys_same_digest(self) -> None:
        """相同密钥的两个服务实例产生相同摘要 — 密钥决定输出。"""

        key1 = b"a" * 32
        key2 = b"b" * 32
        svc1 = TokenDigestService(key1, key2)
        svc2 = TokenDigestService(key1, key2)
        token = "test_token_value_12345"

        assert svc1.digest_access_token(token) == svc2.digest_access_token(token)
        assert svc1.digest_refresh_token(token) == svc2.digest_refresh_token(token)
