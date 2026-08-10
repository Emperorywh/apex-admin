"""HMAC-SHA-256 双密钥摘要服务 — SPEC 12.2 / 23.2.

SPEC 12.2:
  - 数据库只保存 Access Token 和 Refresh Token 的 HMAC-SHA-256 摘要，
    不保存明文 Token。
  - Access Token 和 Refresh Token 使用两个独立的部署密钥计算摘要，
    密钥不得存入数据库。
  - 两个 Token 摘要密钥分别具有至少 256 bit 熵，配置缺失、相同或长度
    不足时应用启动失败。

SPEC 23.2: "密钥轮换必须具有独立管理命令和双密钥短期切换步骤，
不得通过永久 fallback 兼容旧密钥"。

密钥来源为部署配置（``Settings.ACCESS_TOKEN_HMAC_KEY`` 和
``Settings.REFRESH_TOKEN_HMAC_KEY``），构造时执行严格校验。
摘要仅输出 HMAC-SHA-256 十六进制形态，不暴露密钥。

密钥轮换（SPEC 23.2）:
  - 轮换期间同时存在当前密钥和前一代密钥（dual-key）。
  - 新 Token 的摘要始终使用当前密钥计算。
  - 验证 Token 时，先生成当前密钥摘要候选，再在窗口内追加前一代密钥摘要候选。
  - 前一代密钥仅在 ``rotation_expires_at`` 之前有效；超窗后不再产生候选。
  - 不存在永久 fallback——无 ``rotation_expires_at`` 或过期后，
    前一代密钥完全失效。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _utc_now() -> datetime:
    """默认时间提供者 — 返回当前 UTC 时间。"""

    return datetime.now(UTC)


class TokenDigestService:
    """HMAC-SHA-256 双密钥摘要服务 — SPEC 12.2 / 23.2.

    Access Token 和 Refresh Token 使用两个独立部署密钥计算 HMAC-SHA-256
    摘要。数据库只保存摘要，不保存明文 Token。密钥不入数据库。

    构造时执行启动校验（SPEC 12.2: "配置缺失、相同或长度不足时应用
    启动失败"）:
      1. 密钥不得为空（缺失）。
      2. 两把密钥不得相同。
      3. 每把密钥长度 >= 32 字节（256 bit 熵）。

    密钥轮换（SPEC 23.2）:
      构造时可传入 ``previous_access_key`` / ``previous_refresh_key``
      和 ``rotation_expires_at``。轮换期间 ``candidate_access_digests``
      返回当前和前一代两个摘要候选；超窗后只返回当前摘要。
      前一代密钥同样执行长度校验，且不得与当前密钥相同。

    使用方式::

        service = TokenDigestService(access_key, refresh_key)
        access_digest = service.digest_access_token(raw_token)
        refresh_digest = service.digest_refresh_token(raw_token)
    """

    def __init__(
        self,
        access_key: bytes,
        refresh_key: bytes,
        *,
        previous_access_key: bytes | None = None,
        previous_refresh_key: bytes | None = None,
        rotation_expires_at: datetime | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """构造摘要服务并执行密钥启动校验.

        参数:
            access_key:            Access Token HMAC 密钥（>= 32 字节）。
            refresh_key:           Refresh Token HMAC 密钥
                                   （>= 32 字节，与 access_key 不同）。
            previous_access_key:   轮换期间的前一代 Access Token 密钥（可选）。
            previous_refresh_key:  轮换期间的前一代 Refresh Token 密钥（可选）。
            rotation_expires_at:   轮换窗口过期时间（UTC）。前一代密钥在此时间
                                   之前可用于验证，之后失效。为 None 时如果
                                   前一代密钥也不存在则无轮换。
            now_provider:          时间提供者（可注入用于测试），默认系统 UTC。

        抛出:
            TokenDigestValidationError: 密钥缺失、相同或长度不足。
        """

        self._validate_keys(access_key, refresh_key)

        # 前一代密钥校验（仅在提供时执行）
        if previous_access_key is not None:
            self._validate_previous_key(
                previous_access_key,
                access_key,
                label="前一代 Access Token HMAC",
            )
        if previous_refresh_key is not None:
            self._validate_previous_key(
                previous_refresh_key,
                refresh_key,
                label="前一代 Refresh Token HMAC",
            )

        self._access_key = access_key
        self._refresh_key = refresh_key
        self._previous_access_key = previous_access_key
        self._previous_refresh_key = previous_refresh_key
        self._rotation_expires_at = rotation_expires_at
        self._now_provider = now_provider or _utc_now

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

    @staticmethod
    def _validate_previous_key(
        previous_key: bytes,
        current_key: bytes,
        *,
        label: str,
    ) -> None:
        """校验前一代密钥满足 SPEC 12.2 要求.

        前一代密钥同样需要满足 256 bit 熵，且不得与当前密钥相同。
        """

        if len(previous_key) < MIN_KEY_BYTES:
            raise TokenDigestValidationError(
                f"{label} 密钥长度 {len(previous_key)} 字节"
                f"不足，要求至少 {MIN_KEY_BYTES} 字节（256 bit 熵）",
            )
        if previous_key == current_key:
            raise TokenDigestValidationError(
                f"{label} 密钥不得与当前密钥相同",
            )

    def digest_access_token(self, token: str) -> str:
        """计算 Access Token 的 HMAC-SHA-256 摘要 — SPEC 12.2.

        SPEC 12.2: "数据库只保存 Access Token 和 Refresh Token 的
        HMAC-SHA-256 摘要，不保存明文 Token"。

        SPEC 23.2: 新 Token 摘要始终使用当前密钥计算。

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

    # ── 密钥轮换支持 — SPEC 23.2 ───────────────────────────────────────────

    def is_rotation_active(self) -> bool:
        """轮换窗口是否处于活动状态 — SPEC 23.2.

        返回 True 当且仅当:
          - 存在前一代密钥（access 或 refresh），且
          - 设置了过期时间，且当前时间在过期时间之前（含边界）。

        SPEC 23.2: "不得通过永久 fallback 兼容旧密钥"。
        无过期时间时前一代密钥不激活，防止永久 fallback。
        超窗后返回 False——前一代密钥不再有效。
        """

        if self._previous_access_key is None and self._previous_refresh_key is None:
            return False
        if self._rotation_expires_at is None:
            return False  # 无过期 = 无永久 fallback（SPEC 23.2）
        now = self._now_provider()
        return now <= self._rotation_expires_at

    def candidate_access_digests(self, token: str) -> list[str]:
        """返回 Access Token 的全部有效摘要候选 — SPEC 23.2.

        SPEC 23.2: "密钥轮换必须具有...双密钥短期切换步骤"。
        轮换窗口内返回 [当前摘要, 前一代摘要]，超窗后只返回 [当前摘要]。
        无前一代密钥时只返回 [当前摘要]。

        验证方使用每个候选摘要查询数据库，任一命中即验证通过。

        参数:
            token: 明文 Access Token 字符串。

        返回:
            摘要候选列表（当前密钥摘要始终在首位）。
        """

        digests = [self.digest_access_token(token)]
        if self.is_rotation_active() and self._previous_access_key is not None:
            digests.append(
                hmac.new(
                    self._previous_access_key,
                    token.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
            )
        return digests

    def candidate_refresh_digests(self, token: str) -> list[str]:
        """返回 Refresh Token 的全部有效摘要候选 — SPEC 23.2.

        轮换窗口内返回 [当前摘要, 前一代摘要]，超窗后只返回 [当前摘要]。

        参数:
            token: 明文 Refresh Token 字符串。

        返回:
            摘要候选列表（当前密钥摘要始终在首位）。
        """

        digests = [self.digest_refresh_token(token)]
        if self.is_rotation_active() and self._previous_refresh_key is not None:
            digests.append(
                hmac.new(
                    self._previous_refresh_key,
                    token.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
            )
        return digests
