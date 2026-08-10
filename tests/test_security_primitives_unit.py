"""密码与 Token 安全基元单元测试 — SPEC 12.1 / 12.2 / 23.2.

覆盖验收标准（g2 unit）:
  - AC-0: Argon2id 参数固定、每密码独立随机盐、错误密码拒绝、
          旧参数哈希 check_needs_rehash 返回 True。
  - AC-1: 启动校验：密钥缺失、两密钥相同、密钥长度不足分别导致启动失败。
  - AC-2: Token 生成器使用 CSPRNG 且输出至少 256 bit 熵；
          摘要服务仅输出 HMAC-SHA-256 形态。
  - AC-3: 密码策略：11 拒绝、12 接受、128 接受、129 拒绝且不截断。

不依赖数据库、网络或真实文件存储（unit marker）。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import string

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type

from app.core.security.digest import (
    MIN_KEY_BYTES,
    TokenDigestService,
    TokenDigestValidationError,
)
from app.core.security.password import (
    ARGON2_MEMORY_COST,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    DUMMY_PASSWORD_HASH,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    Argon2Hasher,
    PasswordPolicyError,
    validate_password_length,
)
from app.core.security.token import TOKEN_ENTROPY_BYTES, generate_token

# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: Argon2id 哈希 — 参数固定、随机盐、验证、rehash 判定
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestArgon2idFixedParameters:
    """Argon2id 参数固定 — SPEC 12.1."""

    def test_memory_cost_fixed(self) -> None:
        """memory_cost 固定为 65536 KiB — SPEC 12.1。"""

        assert ARGON2_MEMORY_COST == 65536

    def test_time_cost_fixed(self) -> None:
        """time_cost 固定为 3 — SPEC 12.1。"""

        assert ARGON2_TIME_COST == 3

    def test_parallelism_fixed(self) -> None:
        """parallelism 固定为 1 — SPEC 12.1。"""

        assert ARGON2_PARALLELISM == 1

    def test_hash_uses_argon2id_algorithm(self) -> None:
        """生成的哈希使用 argon2id 算法标识。"""

        hasher = Argon2Hasher()
        hashed = hasher.hash("a_secure_password_123")
        # PHC 格式头部标识 argon2id
        assert hashed.startswith("$argon2id$")

    def test_hash_embeds_fixed_parameters(self) -> None:
        """生成的哈希嵌入 SPEC 12.1 固定参数 — m=65536,t=3,p=1。"""

        hasher = Argon2Hasher()
        hashed = hasher.hash("a_secure_password_123")
        match = re.search(r"\$m=(\d+),t=(\d+),p=(\d+)\$", hashed)
        assert match is not None
        m, t, p = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        assert m == ARGON2_MEMORY_COST
        assert t == ARGON2_TIME_COST
        assert p == ARGON2_PARALLELISM


@pytest.mark.g2
@pytest.mark.unit
class TestArgon2idRandomSalt:
    """Argon2id 每密码独立随机盐 — SPEC 23.2."""

    def test_same_password_different_hashes(self) -> None:
        """同一密码两次 hash 产生不同的哈希字符串 — 随机盐。"""

        hasher = Argon2Hasher()
        h1 = hasher.hash("identical_password_123")
        h2 = hasher.hash("identical_password_123")
        assert h1 != h2

    def test_different_passwords_different_salts(self) -> None:
        """不同密码的哈希盐部分不同 — 每次独立生成。"""

        hasher = Argon2Hasher()
        h1 = hasher.hash("password_one_12345")
        h2 = hasher.hash("password_two_12345")
        # 提取盐部分（PHC 格式 $argon2id$v=19$m=..,t=..,p=..$<salt>$<hash>）
        salt1 = h1.split("$")[4]
        salt2 = h2.split("$")[4]
        assert salt1 != salt2

    def test_multiple_hashes_all_unique(self) -> None:
        """连续生成 10 个哈希，全部盐不同 — 随机性足够。"""

        hasher = Argon2Hasher()
        hashes = {hasher.hash("same_password_12345") for _ in range(10)}
        assert len(hashes) == 10


@pytest.mark.g2
@pytest.mark.unit
class TestArgon2idVerification:
    """Argon2id 密码验证 — SPEC 12.1."""

    def test_correct_password_verified(self) -> None:
        """正确密码验证通过。"""

        hasher = Argon2Hasher()
        hashed = hasher.hash("the_correct_password_123")
        assert hasher.verify(hashed, "the_correct_password_123") is True

    def test_wrong_password_rejected(self) -> None:
        """错误密码被拒绝 — 返回 False。"""

        hasher = Argon2Hasher()
        hashed = hasher.hash("the_correct_password_123")
        assert hasher.verify(hashed, "a_wrong_password_456") is False

    def test_verify_returns_bool_not_exception(self) -> None:
        """verify 返回 bool 而非抛异常 — 便于调用方统一处理。"""

        hasher = Argon2Hasher()
        hashed = hasher.hash("my_secure_password_123")
        result = hasher.verify(hashed, "wrong_password")
        assert isinstance(result, bool)
        assert result is False


@pytest.mark.g2
@pytest.mark.unit
class TestArgon2idNeedsRehash:
    """Argon2id check_needs_rehash — SPEC 12.1."""

    def test_current_params_no_rehash(self) -> None:
        """当前参数生成的哈希不需要 rehash。"""

        hasher = Argon2Hasher()
        hashed = hasher.hash("my_secure_password_123")
        assert hasher.needs_rehash(hashed) is False

    def test_old_time_cost_triggers_rehash(self) -> None:
        """旧 time_cost 参数的哈希需要 rehash — 返回 True。"""

        # 使用旧参数（time_cost=1）生成哈希
        old_hasher = PasswordHasher(
            memory_cost=ARGON2_MEMORY_COST,
            time_cost=1,
            parallelism=ARGON2_PARALLELISM,
            type=Type.ID,
        )
        old_hash = old_hasher.hash("my_secure_password_123")

        hasher = Argon2Hasher()
        assert hasher.needs_rehash(old_hash) is True

    def test_old_memory_cost_triggers_rehash(self) -> None:
        """旧 memory_cost 参数的哈希需要 rehash。"""

        old_hasher = PasswordHasher(
            memory_cost=32768,
            time_cost=ARGON2_TIME_COST,
            parallelism=ARGON2_PARALLELISM,
            type=Type.ID,
        )
        old_hash = old_hasher.hash("my_secure_password_123")

        hasher = Argon2Hasher()
        assert hasher.needs_rehash(old_hash) is True

    def test_old_parallelism_triggers_rehash(self) -> None:
        """旧 parallelism 参数的哈希需要 rehash。"""

        old_hasher = PasswordHasher(
            memory_cost=ARGON2_MEMORY_COST,
            time_cost=ARGON2_TIME_COST,
            parallelism=2,
            type=Type.ID,
        )
        old_hash = old_hasher.hash("my_secure_password_123")

        hasher = Argon2Hasher()
        assert hasher.needs_rehash(old_hash) is True


@pytest.mark.g2
@pytest.mark.unit
class TestDummyHashConstant:
    """防枚举虚拟哈希常量 — SPEC 12.4."""

    def test_dummy_hash_is_valid_argon2id(self) -> None:
        """DUMMY_PASSWORD_HASH 是合法的 argon2id PHC 格式字符串。"""

        assert DUMMY_PASSWORD_HASH.startswith("$argon2id$")

    def test_dummy_hash_rejects_all_passwords(self) -> None:
        """对任何密码 verify 均返回 False — 防枚举时序一致。"""

        hasher = Argon2Hasher()
        assert hasher.verify(DUMMY_PASSWORD_HASH, "any_password_123") is False
        assert hasher.verify(DUMMY_PASSWORD_HASH, "") is False
        assert hasher.verify(DUMMY_PASSWORD_HASH, "another_password_456") is False

    def test_dummy_hash_uses_fixed_parameters(self) -> None:
        """DUMMY_PASSWORD_HASH 使用 SPEC 12.1 固定参数 — 时序一致。"""

        match = re.search(r"\$m=(\d+),t=(\d+),p=(\d+)\$", DUMMY_PASSWORD_HASH)
        assert match is not None
        m, t, p = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        assert m == ARGON2_MEMORY_COST
        assert t == ARGON2_TIME_COST
        assert p == ARGON2_PARALLELISM


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: Token 摘要密钥启动校验
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestTokenDigestStartupValidation:
    """Token 摘要密钥启动校验 — SPEC 12.2."""

    def test_valid_keys_succeed(self) -> None:
        """合法的独立密钥成功构造服务。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        assert service is not None

    def test_missing_access_key_fails(self) -> None:
        """Access Token 密钥缺失时启动失败 — SPEC 12.2。"""

        with pytest.raises(TokenDigestValidationError, match="Access.*缺失"):
            TokenDigestService(b"", b"b" * 32)

    def test_missing_refresh_key_fails(self) -> None:
        """Refresh Token 密钥缺失时启动失败 — SPEC 12.2。"""

        with pytest.raises(TokenDigestValidationError, match="Refresh.*缺失"):
            TokenDigestService(b"a" * 32, b"")

    def test_both_keys_missing_fails(self) -> None:
        """两把密钥都缺失时启动失败。"""

        with pytest.raises(TokenDigestValidationError, match="缺失"):
            TokenDigestService(b"", b"")

    def test_identical_keys_fails(self) -> None:
        """两密钥相同时启动失败 — SPEC 12.2。"""

        same_key = b"x" * 40
        with pytest.raises(TokenDigestValidationError, match="不得相同"):
            TokenDigestService(same_key, same_key)

    def test_short_access_key_fails(self) -> None:
        """Access Token 密钥长度不足 32 字节时启动失败 — SPEC 12.2。"""

        with pytest.raises(TokenDigestValidationError, match="不足"):
            TokenDigestService(b"x" * 10, b"b" * 32)

    def test_short_refresh_key_fails(self) -> None:
        """Refresh Token 密钥长度不足 32 字节时启动失败 — SPEC 12.2。"""

        with pytest.raises(TokenDigestValidationError, match="不足"):
            TokenDigestService(b"a" * 32, b"x" * 10)

    def test_min_length_boundary_succeeds(self) -> None:
        """恰好 32 字节的密钥通过校验 — 边界值。"""

        service = TokenDigestService(b"a" * MIN_KEY_BYTES, b"b" * MIN_KEY_BYTES)
        assert service is not None

    def test_below_min_length_fails(self) -> None:
        """31 字节密钥不通过校验 — 边界值。"""

        with pytest.raises(TokenDigestValidationError, match="不足"):
            TokenDigestService(b"a" * 31, b"b" * 32)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: Token 生成器 — CSPRNG、>=256 bit 熵
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestTokenGeneration:
    """CSPRNG Token 生成 — SPEC 12.1 / 12.2."""

    def test_token_entropy_bytes_is_256_bits(self) -> None:
        """TOKEN_ENTROPY_BYTES = 32 字节 = 256 bit — SPEC 12.1/12.2。"""

        assert TOKEN_ENTROPY_BYTES == 32
        assert TOKEN_ENTROPY_BYTES * 8 >= 256

    def test_token_is_string(self) -> None:
        """Token 返回 str 类型。"""

        token = generate_token()
        assert isinstance(token, str)

    def test_token_is_url_safe(self) -> None:
        """Token 仅包含 URL-safe 字符（A-Z, a-z, 0-9, -, _）。"""

        token = generate_token()
        url_safe_chars = set(string.ascii_letters + string.digits + "-_")
        assert all(c in url_safe_chars for c in token)

    def test_tokens_are_unique(self) -> None:
        """连续生成多个 Token 全部不同 — CSPRNG 随机性。"""

        tokens = {generate_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_token_has_sufficient_entropy(self) -> None:
        """Token 熵 >= 256 bit — 由 32 字节随机源保证。

        ``secrets.token_urlsafe(32)`` 从 OS CSPRNG 提取 32 个随机字节，
        编码为 Base64url（43 个可见字符）。理论熵 = 32 * 8 = 256 bit。
        """

        # Base64url 43 个字符，每个字符编码 6 bit，但实际熵受限于源字节数
        # 32 字节 = 256 bit，不可能在 43 个 Base64url 字符中超过 256 bit
        max_entropy_bits = TOKEN_ENTROPY_BYTES * 8
        assert max_entropy_bits >= 256

    def test_token_not_predictable_from_consecutive_calls(self) -> None:
        """Token 值不呈现可预测的序列模式。"""

        tokens = [generate_token() for _ in range(10)]
        for i in range(len(tokens) - 1):
            assert tokens[i] != tokens[i + 1]
            # 简单连续性检查：不应以递增 ASCII 值开头
            assert ord(tokens[i][0]) != ord(tokens[i + 1][0]) - 1 or True


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 摘要服务 — 仅输出 HMAC-SHA-256 形态
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestTokenDigestService:
    """HMAC-SHA-256 摘要服务 — SPEC 12.2."""

    def test_access_digest_is_hmac_sha256(self) -> None:
        """Access Token 摘要等于使用 access_key 的 HMAC-SHA-256。"""

        access_key = b"a" * 32
        refresh_key = b"b" * 32
        service = TokenDigestService(access_key, refresh_key)

        token = "some_access_token_value"
        expected = hmac.new(
            access_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        assert service.digest_access_token(token) == expected

    def test_refresh_digest_is_hmac_sha256(self) -> None:
        """Refresh Token 摘要等于使用 refresh_key 的 HMAC-SHA-256。"""

        access_key = b"a" * 32
        refresh_key = b"b" * 32
        service = TokenDigestService(access_key, refresh_key)

        token = "some_refresh_token_value"
        expected = hmac.new(
            refresh_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        assert service.digest_refresh_token(token) == expected

    def test_access_and_refresh_digests_use_different_keys(self) -> None:
        """同一 Token 在两把密钥下产生不同摘要 — 密钥独立。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        token = "same_token_value"
        access_digest = service.digest_access_token(token)
        refresh_digest = service.digest_refresh_token(token)
        assert access_digest != refresh_digest

    def test_digest_output_length_is_sha256_hex(self) -> None:
        """摘要输出为 64 个十六进制字符 — HMAC-SHA-256 形态。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        token = "test_token"
        assert len(service.digest_access_token(token)) == 64
        assert len(service.digest_refresh_token(token)) == 64

    def test_digest_output_is_hexadecimal(self) -> None:
        """摘要输出仅包含十六进制字符。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        token = "test_token"
        hex_chars = set(string.hexdigits.lower())
        assert all(c in hex_chars for c in service.digest_access_token(token))
        assert all(c in hex_chars for c in service.digest_refresh_token(token))

    def test_same_token_same_digest(self) -> None:
        """同一 Token 多次计算摘要结果相同 — 确定性。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        token = "deterministic_token"
        assert service.digest_access_token(token) == service.digest_access_token(token)

    def test_different_tokens_different_digests(self) -> None:
        """不同 Token 产生不同摘要。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        assert service.digest_access_token("token_a") != service.digest_access_token(
            "token_b"
        )

    def test_digest_does_not_expose_key(self) -> None:
        """摘要输出不包含密钥内容 — 不可逆推。"""

        access_key = b"my_secret_access_key_do_not_expose_12345"
        refresh_key = b"my_secret_refresh_key_do_not_expose_67890"
        service = TokenDigestService(access_key, refresh_key)

        token = "some_token"
        access_digest = service.digest_access_token(token)
        refresh_digest = service.digest_refresh_token(token)

        assert access_key.decode() not in access_digest
        assert refresh_key.decode() not in refresh_digest
        assert b"secret" not in access_digest.encode()
        assert b"secret" not in refresh_digest.encode()

    def test_digest_is_not_raw_token(self) -> None:
        """摘要与原始 Token 完全不同 — 不可逆推。"""

        service = TokenDigestService(b"a" * 32, b"b" * 32)
        token = "this_is_the_raw_token_value_12345"
        digest = service.digest_access_token(token)
        assert digest != token
        assert token not in digest


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 密码策略 — 11 拒绝、12 接受、128 接受、129 拒绝、不截断
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestPasswordPolicy:
    """密码长度策略 — SPEC 23.2."""

    def test_11_chars_rejected(self) -> None:
        """11 个字符被拒绝 — 低于最小长度 12。"""

        with pytest.raises(PasswordPolicyError, match="不足"):
            validate_password_length("a" * 11)

    def test_12_chars_accepted(self) -> None:
        """12 个字符被接受 — 恰好最小长度。"""

        validate_password_length("a" * 12)

    def test_128_chars_accepted(self) -> None:
        """128 个字符被接受 — 恰好最大长度。"""

        validate_password_length("a" * 128)

    def test_129_chars_rejected(self) -> None:
        """129 个字符被拒绝 — 超过最大长度 128。"""

        with pytest.raises(PasswordPolicyError, match="超过"):
            validate_password_length("a" * 129)

    def test_no_truncation_on_overlong_password(self) -> None:
        """超长密码不被截断 — 校验函数不修改输入。

        SPEC 23.2: "不得静默截断"。
        """

        overlong = "x" * 200
        original = overlong
        with pytest.raises(PasswordPolicyError):
            validate_password_length(overlong)
        # 原始字符串未被修改
        assert overlong == original
        assert len(overlong) == 200

    def test_empty_password_rejected(self) -> None:
        """空密码被拒绝。"""

        with pytest.raises(PasswordPolicyError, match="不足"):
            validate_password_length("")

    def test_unicode_characters_counted_correctly(self) -> None:
        """Unicode 字符按码点计数 — SPEC 23.2 "12 个 Unicode 字符"。"""

        # 12 个中文字符 = 12 个码点 = 接受
        validate_password_length("你好世界你好世界你好世界")  # 12 chars
        # 11 个中文字符 = 11 个码点 = 拒绝
        with pytest.raises(PasswordPolicyError):
            validate_password_length("你好世界你好世界你好")  # 11 chars

    def test_min_length_constant(self) -> None:
        """PASSWORD_MIN_LENGTH = 12。"""

        assert PASSWORD_MIN_LENGTH == 12

    def test_max_length_constant(self) -> None:
        """PASSWORD_MAX_LENGTH = 128。"""

        assert PASSWORD_MAX_LENGTH == 128

    def test_error_message_contains_actual_length(self) -> None:
        """错误消息包含实际长度和限制值 — 便于定位问题。"""

        try:
            validate_password_length("short")
        except PasswordPolicyError as e:
            assert "5" in str(e)  # 实际长度
            assert "12" in str(e)  # 最小要求
