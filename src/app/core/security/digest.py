"""HMAC-SHA-256 双密钥摘要服务 — SPEC 12.2.

SPEC 12.2:
  - 数据库只保存 Access Token 和 Refresh Token 的 HMAC-SHA-256 摘要，
    不保存明文 Token。
  - Access Token 和 Refresh Token 使用两个独立的部署密钥计算摘要，
    密钥不得存入数据库。
  - 两个 Token 摘要密钥分别具有至少 256 bit 熵，配置缺失、相同或长度
    不足时应用启动失败。

密钥来源为部署配置（``Settings.ACCESS_TOKEN_HMAC_KEY`` 和
``Settings.REFRESH_TOKEN_HMAC_KEY``），构造时执行严格校验。
摘要仅输出 HMAC-SHA-256 十六进制形态，不暴露密钥。
"""

from __future__ import annotations

import hashlib
import hmac

# ── 密钥最小长度（SPEC 12.2）──────────────────────────────────────────────
#
# SPEC 12.2: "两个 Token 摘要密钥分别具有至少 256 bit 熵"。
# 256 bit = 32 字节。

MIN_KEY_BYTES: int = 32
"""Token HMAC 密钥最小字节数 — 32 字节 = 256 bit 熵，SPEC 12.2。"""


class TokenDigestValidationError(ValueError):
    """Token 摘要密钥启动校验失败 — SPEC 12.2.

    密钥缺失、两密钥相同或长度不足时抛出此异常，导致应用启动失败。
    使用 ValueError 子类，与部署配置校验一致。
    """


class TokenDigestService:
    """HMAC-SHA-256 双密钥摘要服务 — SPEC 12.2.

    Access Token 和 Refresh Token 使用两个独立部署密钥计算 HMAC-SHA-256
    摘要。数据库只保存摘要，不保存明文 Token。密钥不入数据库。

    构造时执行启动校验（SPEC 12.2: "配置缺失、相同或长度不足时应用
    启动失败"）:
      1. 密钥不得为空（缺失）。
      2. 两把密钥不得相同。
      3. 每把密钥长度 >= 32 字节（256 bit 熵）。

    使用方式::

        service = TokenDigestService(access_key, refresh_key)
        access_digest = service.digest_access_token(raw_token)
        refresh_digest = service.digest_refresh_token(raw_token)
    """

    def __init__(self, access_key: bytes, refresh_key: bytes) -> None:
        """构造摘要服务并执行密钥启动校验.

        参数:
            access_key:  Access Token HMAC 密钥（>= 32 字节）。
            refresh_key: Refresh Token HMAC 密钥（>= 32 字节，与 access_key 不同）。

        抛出:
            TokenDigestValidationError: 密钥缺失、相同或长度不足。
        """

        self._validate_keys(access_key, refresh_key)
        self._access_key = access_key
        self._refresh_key = refresh_key

    @staticmethod
    def _validate_keys(access_key: bytes, refresh_key: bytes) -> None:
        """校验两把密钥满足 SPEC 12.2 要求.

        SPEC 12.2: "配置缺失、相同或长度不足时应用启动失败"。

        参数:
            access_key:  Access Token HMAC 密钥。
            refresh_key: Refresh Token HMAC 密钥。

        抛出:
            TokenDigestValidationError: 密钥缺失、相同或长度不足。
        """

        # 1. 密钥不得为空（缺失）
        if len(access_key) == 0:
            raise TokenDigestValidationError(
                "Access Token HMAC 密钥缺失",
            )
        if len(refresh_key) == 0:
            raise TokenDigestValidationError(
                "Refresh Token HMAC 密钥缺失",
            )

        # 2. 两把密钥不得相同
        if access_key == refresh_key:
            raise TokenDigestValidationError(
                "Access Token 与 Refresh Token HMAC 密钥不得相同",
            )

        # 3. 每把密钥长度 >= 32 字节（256 bit 熵）
        if len(access_key) < MIN_KEY_BYTES:
            raise TokenDigestValidationError(
                f"Access Token HMAC 密钥长度 {len(access_key)} 字节"
                f"不足，要求至少 {MIN_KEY_BYTES} 字节（256 bit 熵）",
            )
        if len(refresh_key) < MIN_KEY_BYTES:
            raise TokenDigestValidationError(
                f"Refresh Token HMAC 密钥长度 {len(refresh_key)} 字节"
                f"不足，要求至少 {MIN_KEY_BYTES} 字节（256 bit 熵）",
            )

    def digest_access_token(self, token: str) -> str:
        """计算 Access Token 的 HMAC-SHA-256 摘要 — SPEC 12.2.

        SPEC 12.2: "数据库只保存 Access Token 和 Refresh Token 的
        HMAC-SHA-256 摘要，不保存明文 Token"。

        参数:
            token: 明文 Access Token 字符串。

        返回:
            HMAC-SHA-256 十六进制摘要字符串（64 字符）。
        """

        return hmac.new(
            self._access_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def digest_refresh_token(self, token: str) -> str:
        """计算 Refresh Token 的 HMAC-SHA-256 摘要 — SPEC 12.2.

        SPEC 12.2: "数据库只保存 ... HMAC-SHA-256 摘要"。
        使用与 Access Token 独立的密钥计算。

        参数:
            token: 明文 Refresh Token 字符串。

        返回:
            HMAC-SHA-256 十六进制摘要字符串（64 字符）。
        """

        return hmac.new(
            self._refresh_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
