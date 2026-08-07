"""Token 生成与 HMAC 摘要服务（SPEC §12.1、§12.2）。

提供不透明随机 Token 生成和 HMAC-SHA-256 摘要计算：
- ``TokenGenerator`` 使用密码学安全随机数生成器产生至少 256 bit 熵的不透明 Token。
- ``TokenDigester`` 使用两个独立的部署密钥分别计算 Access Token 和 Refresh Token
  的 HMAC-SHA-256 摘要。数据库只保存摘要，不保存明文 Token（SPEC §12.2）。

两个密钥从部署配置加载（SPEC §12.2），缺失、相同或长度不足时
由 :class:`~app.config.settings.Settings` 校验使启动失败。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from pydantic import SecretStr

# Token 随机字节数——256 bit（SPEC §12.1、§12.2）
_TOKEN_BYTES: int = 32


class TokenGenerator:
    """不透明随机 Token 生成器（SPEC §12.1、§12.2）。

    使用 ``secrets.token_urlsafe`` 生成密码学安全的 URL-safe 随机字符串，
    熵不低于 256 bit（32 字节）。G2 不使用 JWT（SPEC §12.1），Token 为不透明值。

    生成的 Token 为 base64url 编码字符串（约 43 个字符），由调用方在
    响应体（Access Token）或 HttpOnly Cookie（Refresh Token）中返回一次。
    """

    #: Token 随机字节数（256 bit）
    TOKEN_BYTES: int = _TOKEN_BYTES

    def generate(self) -> str:
        """生成一个随机不透明 Token。

        Returns:
            URL-safe base64 随机字符串（至少 256 bit 熵）
        """
        return secrets.token_urlsafe(self.TOKEN_BYTES)


def _key_to_bytes(secret: SecretStr) -> bytes:
    """将 :class:`~pydantic.SecretStr` 转换为 HMAC 密钥字节。

    优先尝试 hex 解码（``openssl rand -hex 32`` 生成的密钥），
    非 hex 格式时使用 UTF-8 编码。
    """
    raw = secret.get_secret_value()
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


class TokenDigester:
    """Token HMAC-SHA-256 摘要计算服务（SPEC §12.2）。

    使用两个独立的部署密钥分别计算 Access Token 和 Refresh Token 的摘要。
    数据库只保存摘要（hex 编码），不保存明文 Token（SPEC §12.2）。

    Access Token 和 Refresh Token 使用不同密钥，即使一个密钥泄露
    也不会危及另一种 Token（SPEC §12.2）。

    Args:
        access_key: Access Token HMAC 密钥（从 ``access_token_hmac_key`` 加载）
        refresh_key: Refresh Token HMAC 密钥（从 ``refresh_token_hmac_key`` 加载）
    """

    #: HMAC 使用的哈希算法
    _HASH_ALGORITHM: str = "sha256"

    def __init__(
        self,
        access_key: SecretStr,
        refresh_key: SecretStr,
    ) -> None:
        self._access_key = _key_to_bytes(access_key)
        self._refresh_key = _key_to_bytes(refresh_key)

    def access_digest(self, token: str) -> str:
        """计算 Access Token 的 HMAC-SHA-256 摘要（SPEC §12.2）。

        Args:
            token: Access Token 明文

        Returns:
            hex 编码的 HMAC-SHA-256 摘要
        """
        return hmac.new(
            self._access_key,
            token.encode("utf-8"),
            self._HASH_ALGORITHM,
        ).hexdigest()

    def refresh_digest(self, token: str) -> str:
        """计算 Refresh Token 的 HMAC-SHA-256 摘要（SPEC §12.2）。

        使用与 Access Token 不同的独立密钥。

        Args:
            token: Refresh Token 明文

        Returns:
            hex 编码的 HMAC-SHA-256 摘要
        """
        return hmac.new(
            self._refresh_key,
            token.encode("utf-8"),
            self._HASH_ALGORITHM,
        ).hexdigest()

    def verify_access(self, token: str, expected_digest: str) -> bool:
        """恒定时间比较 Access Token 摘要。

        Args:
            token: 待验证的 Access Token 明文
            expected_digest: 数据库中存储的摘要

        Returns:
            匹配返回 ``True``
        """
        actual = self.access_digest(token)
        return hmac.compare_digest(actual, expected_digest)

    def verify_refresh(self, token: str, expected_digest: str) -> bool:
        """恒定时间比较 Refresh Token 摘要。"""
        actual = self.refresh_digest(token)
        return hmac.compare_digest(actual, expected_digest)


#: HMAC-SHA-256 摘要的 hex 长度（用于数据库列长度参考）
DIGEST_HEX_LENGTH: int = len(hashlib.sha256(b"").hexdigest())  # 64
