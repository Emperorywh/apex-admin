"""敏感配置加密服务 — SPEC 16.1 / 23.2.

SPEC 23.2:
  - "敏感配置的加密密钥与密文分离管理"。
  - "Token HMAC 密钥和敏感配置加密密钥来自部署配置且彼此独立"。
  - "密钥轮换必须具有独立管理命令和双密钥短期切换步骤，
     不得通过永久 fallback 兼容旧密钥"。

SPEC 16.1: "敏感配置加密存储且默认不回显"。

使用 Fernet 对称加密（AES-128-CBC + HMAC-SHA-256 认证）。
密钥来自部署配置 ``Settings.SYSCONFIG_ENCRYPTION_KEY``，
构造时执行严格校验——密钥缺失或不安全时启动失败。

密钥轮换（SPEC 23.2）:
  - 轮换期间同时存在当前密钥和前一代密钥（dual-key）。
  - 加密始终使用当前密钥。
  - 解密时先尝试当前密钥，再尝试前一代密钥（窗口期内）。
  - ``re-encrypt`` 命令用 ``MultiFernet.rotate`` 将旧密文重加密为新密钥。
  - 不存在永久 fallback——前一代密钥仅用于短期过渡。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet


class ConfigEncryptionError(Exception):
    """配置加密服务错误."""


class ConfigEncryptionService:
    """敏感配置 Fernet 加密服务 — SPEC 16.1 / 23.2.

    SPEC 23.2: "敏感配置的加密密钥与密文分离管理"。
    加密密钥来自部署配置，不存入数据库。密文存储在 ``stored_value`` 列。

    构造时校验密钥合法性（SPEC 7.1: "必需配置缺失时快速失败"）。
    轮换期间支持双密钥短期切换（SPEC 23.2）。

    使用方式::

        service = ConfigEncryptionService(current_key, previous_key)
        ciphertext = service.encrypt("plaintext-value")
        plaintext = service.decrypt(ciphertext)
    """

    def __init__(
        self,
        current_key: str,
        previous_key: str | None = None,
    ) -> None:
        """构造加密服务并校验密钥合法性.

        参数:
            current_key:  当前 Fernet 密钥（url-safe base64 编码的 32 字节）。
            previous_key: 轮换期间的前一代密钥（可选）。

        抛出:
            ConfigEncryptionError: 密钥缺失或格式不合法。
        """

        if not current_key:
            raise ConfigEncryptionError("敏感配置加密密钥缺失")

        try:
            self._current_fernet = Fernet(current_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise ConfigEncryptionError(
                f"当前敏感配置加密密钥不合法: {exc}",
            ) from exc

        self._previous_fernet: Fernet | None = None
        if previous_key is not None:
            if not previous_key:
                raise ConfigEncryptionError("前一代敏感配置加密密钥为空")
            if previous_key == current_key:
                raise ConfigEncryptionError(
                    "前一代敏感配置加密密钥不得与当前密钥相同",
                )
            try:
                self._previous_fernet = Fernet(previous_key.encode("utf-8"))
            except (ValueError, TypeError) as exc:
                raise ConfigEncryptionError(
                    f"前一代敏感配置加密密钥不合法: {exc}",
                ) from exc

        # MultiFernet 用于解密时按序尝试多个密钥。
        # 加密始终使用第一个密钥（当前密钥）。
        fernets: list[Fernet] = [self._current_fernet]
        if self._previous_fernet is not None:
            fernets.append(self._previous_fernet)
        self._multi_fernet = MultiFernet(fernets)

    @property
    def has_previous_key(self) -> bool:
        """是否配置了前一代密钥（轮换窗口期）。"""

        return self._previous_fernet is not None

    def encrypt(self, plaintext: str) -> str:
        """加密明文 — SPEC 16.1 / 23.2.

        始终使用当前密钥加密（SPEC 23.2: 新密文始终使用当前密钥）。

        参数:
            plaintext: 待加密的明文配置值。

        返回:
            Fernet 密文字符串。
        """

        return self._current_fernet.encrypt(plaintext.encode("utf-8")).decode(
            "ascii",
        )

    def decrypt(self, ciphertext: str) -> str:
        """解密密文 — SPEC 16.1 / 23.2.

        轮换窗口期内，先尝试当前密钥，再尝试前一代密钥。
        无永久 fallback——未配置前一代密钥时只尝试当前密钥。

        参数:
            ciphertext: Fernet 密文字符串。

        返回:
            解密后的明文配置值。

        抛出:
            ConfigEncryptionError: 解密失败（密钥不匹配或密文损坏）。
        """

        try:
            return self._multi_fernet.decrypt(
                ciphertext.encode("utf-8"),
            ).decode("utf-8")
        except Exception as exc:
            raise ConfigEncryptionError(
                f"敏感配置解密失败: {exc}",
            ) from exc

    def rotate(self, ciphertext: str) -> str:
        """重加密密文 — SPEC 23.2 密钥轮换 re-encrypt.

        使用 ``MultiFernet.rotate`` 将旧密文重加密为当前密钥的密文。
        解密时按序尝试所有密钥（当前 → 前一代），加密始终使用当前密钥。

        SPEC 23.2: "密钥轮换必须具有独立管理命令和双密钥短期切换步骤，
        不得通过永久 fallback 兼容旧密钥"。

        参数:
            ciphertext: 旧密文字符串（可能由前一代密钥加密）。

        返回:
            当前密钥加密的新密文字符串。

        抛出:
            ConfigEncryptionError: 解密失败（无密钥能解密此密文）。
        """

        try:
            return self._multi_fernet.rotate(
                ciphertext.encode("utf-8"),
            ).decode("ascii")
        except Exception as exc:
            raise ConfigEncryptionError(
                f"密钥轮换重加密失败: {exc}",
            ) from exc

    @staticmethod
    def generate_key() -> str:
        """生成新的 Fernet 密钥 — 供 CLI 轮换命令使用.

        SPEC 23.2: "密钥不得提交到版本控制"。
        生成的密钥应通过环境变量配置，不直接写入代码或数据库。
        """

        return Fernet.generate_key().decode("ascii")
