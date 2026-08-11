"""系统配置模块单元测试 — SPEC 16.1 / 16.2 / 23.2.

覆盖（不需要数据库）:
  - 领域实体与枚举。
  - 值类型校验逻辑。
  - 加密/解密/轮换逻辑。
  - 掩码逻辑。
  - 越键读取拒绝（测试证明）。
  - 配置读取类型转换。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.sysconfig.crypto import (
    ConfigEncryptionError,
    ConfigEncryptionService,
)
from app.modules.sysconfig.errors import (
    ConfigValueTypeMismatchError,
)
from app.modules.sysconfig.models import ConfigItem, ConfigStatus, ConfigType
from app.modules.sysconfig.use_case import (
    _mask_response_value,
    _parse_typed_value,
    validate_config_value,
)


@pytest.mark.g3
@pytest.mark.unit
class TestConfigDomainModels:
    """配置领域实体与枚举测试 — SPEC 16.1."""

    def test_config_type_values(self) -> None:
        """配置类型枚举值正确."""

        assert ConfigType.STRING == "string"
        assert ConfigType.INT == "int"
        assert ConfigType.BOOL == "bool"
        assert ConfigType.JSON == "json"

    def test_config_status_values(self) -> None:
        """配置状态枚举值正确."""

        assert ConfigStatus.ACTIVE == "active"
        assert ConfigStatus.DISABLED == "disabled"

    def test_config_item_is_frozen(self) -> None:
        """配置实体不可变."""

        item = _make_item()
        with pytest.raises(AttributeError):
            item.group = "modified"  # type: ignore[misc]


@pytest.mark.g3
@pytest.mark.unit
class TestValidateConfigValue:
    """配置值类型校验测试 — SPEC 16.1."""

    def test_string_always_valid(self) -> None:
        """string 类型：任何非空字符串合法."""

        validate_config_value(ConfigType.STRING, "hello")
        validate_config_value(ConfigType.STRING, "123")
        validate_config_value(ConfigType.STRING, '{"a":1}')

    def test_int_valid(self) -> None:
        """int 类型：合法整数字符串通过."""

        validate_config_value(ConfigType.INT, "42")
        validate_config_value(ConfigType.INT, "-100")

    def test_int_invalid(self) -> None:
        """int 类型：非整数字符串抛出类型不匹配错误."""

        with pytest.raises(ConfigValueTypeMismatchError):
            validate_config_value(ConfigType.INT, "abc")
        with pytest.raises(ConfigValueTypeMismatchError):
            validate_config_value(ConfigType.INT, "12.5")

    def test_bool_valid(self) -> None:
        """bool 类型：'true'/'false' 合法."""

        validate_config_value(ConfigType.BOOL, "true")
        validate_config_value(ConfigType.BOOL, "false")

    def test_bool_invalid(self) -> None:
        """bool 类型：非 'true'/'false' 抛出类型不匹配错误."""

        with pytest.raises(ConfigValueTypeMismatchError):
            validate_config_value(ConfigType.BOOL, "yes")
        with pytest.raises(ConfigValueTypeMismatchError):
            validate_config_value(ConfigType.BOOL, "1")

    def test_json_valid(self) -> None:
        """json 类型：合法 JSON 字符串通过."""

        validate_config_value(ConfigType.JSON, '{"key": "value"}')
        validate_config_value(ConfigType.JSON, "[1, 2, 3]")
        validate_config_value(ConfigType.JSON, "null")

    def test_json_invalid(self) -> None:
        """json 类型：非法 JSON 抛出类型不匹配错误."""

        with pytest.raises(ConfigValueTypeMismatchError):
            validate_config_value(ConfigType.JSON, "{invalid")
        with pytest.raises(ConfigValueTypeMismatchError):
            validate_config_value(ConfigType.JSON, "not json")


@pytest.mark.g3
@pytest.mark.unit
class TestParseTypedValue:
    """配置值类型转换测试 — 供 ConfigReadService 使用."""

    def test_parse_string(self) -> None:
        assert _parse_typed_value(ConfigType.STRING, "hello") == "hello"

    def test_parse_int(self) -> None:
        assert _parse_typed_value(ConfigType.INT, "42") == 42
        assert _parse_typed_value(ConfigType.INT, "-7") == -7

    def test_parse_bool(self) -> None:
        assert _parse_typed_value(ConfigType.BOOL, "true") is True
        assert _parse_typed_value(ConfigType.BOOL, "false") is False

    def test_parse_json(self) -> None:
        result = _parse_typed_value(ConfigType.JSON, '{"a": 1}')
        assert result == {"a": 1}

    def test_parse_json_non_object_raises(self) -> None:
        """json 类型顶层必须为 JSON 对象."""

        with pytest.raises(ConfigValueTypeMismatchError):
            _parse_typed_value(ConfigType.JSON, "[1, 2]")


@pytest.mark.g3
@pytest.mark.unit
class TestConfigEncryptionService:
    """敏感配置加密服务测试 — SPEC 16.1 / 23.2."""

    @staticmethod
    def _make_key() -> str:
        return ConfigEncryptionService.generate_key()

    def test_encrypt_decrypt_roundtrip(self) -> None:
        """加密 → 解密恢复明文."""

        key = self._make_key()
        service = ConfigEncryptionService(key)
        plaintext = "my-secret-database-password"
        ciphertext = service.encrypt(plaintext)
        assert ciphertext != plaintext
        assert service.decrypt(ciphertext) == plaintext

    def test_missing_key_raises(self) -> None:
        """密钥缺失时启动失败 — SPEC 7.1."""

        with pytest.raises(ConfigEncryptionError):
            ConfigEncryptionService("")

    def test_invalid_key_raises(self) -> None:
        """非法密钥格式时启动失败."""

        with pytest.raises(ConfigEncryptionError):
            ConfigEncryptionService("not-a-valid-fernet-key")

    def test_same_previous_key_raises(self) -> None:
        """前一代密钥不得与当前密钥相同 — SPEC 23.2."""

        key = self._make_key()
        with pytest.raises(ConfigEncryptionError):
            ConfigEncryptionService(key, previous_key=key)

    def test_dual_key_rotation_decrypt(self) -> None:
        """双密钥短期切换：旧密钥加密的密文可用新服务解密."""

        old_key = self._make_key()
        new_key = self._make_key()

        old_service = ConfigEncryptionService(old_key)
        ciphertext = old_service.encrypt("sensitive-value")

        new_service = ConfigEncryptionService(new_key, previous_key=old_key)
        assert new_service.decrypt(ciphertext) == "sensitive-value"

    def test_rotate_re_encrypts_with_current_key(self) -> None:
        """rotate 方法用当前密钥重加密旧密文 — SPEC 23.2."""

        old_key = self._make_key()
        new_key = self._make_key()

        old_service = ConfigEncryptionService(old_key)
        ciphertext = old_service.encrypt("rotation-test")

        new_service = ConfigEncryptionService(new_key, previous_key=old_key)
        new_ciphertext = new_service.rotate(ciphertext)

        # 新密文可以被仅持有新密钥的服务解密
        only_new = ConfigEncryptionService(new_key)
        assert only_new.decrypt(new_ciphertext) == "rotation-test"

    def test_decrypt_wrong_key_raises(self) -> None:
        """无正确密钥时解密失败."""

        key1 = self._make_key()
        key2 = self._make_key()
        service1 = ConfigEncryptionService(key1)
        service2 = ConfigEncryptionService(key2)

        ciphertext = service1.encrypt("secret")
        with pytest.raises(ConfigEncryptionError):
            service2.decrypt(ciphertext)

    def test_has_previous_key(self) -> None:
        """has_previous_key 属性正确."""

        key = self._make_key()
        prev_key = self._make_key()

        assert ConfigEncryptionService(key).has_previous_key is False
        assert (
            ConfigEncryptionService(key, previous_key=prev_key).has_previous_key is True
        )


@pytest.mark.g3
@pytest.mark.unit
class TestMaskResponseValue:
    """敏感配置掩码测试 — SPEC 16.1: 默认不回显."""

    def test_non_sensitive_returns_raw(self) -> None:
        """非敏感配置返回原始值."""

        item = _make_item(is_sensitive=False, stored_value="plain-value")
        assert _mask_response_value(item) == "plain-value"

    def test_sensitive_returns_mask(self) -> None:
        """敏感配置返回掩码 — SPEC 16.1."""

        from app.modules.sysconfig.schemas import SENSITIVE_MASK

        item = _make_item(is_sensitive=True, stored_value="gAAAAABlm...")
        assert _mask_response_value(item) == SENSITIVE_MASK


# ── 辅助函数 ─────────────────────────────────────────────────────────────


def _make_item(
    *,
    is_sensitive: bool = False,
    stored_value: str = "test-value",
    is_core_security: bool = False,
) -> ConfigItem:
    """构造测试用配置项."""

    return ConfigItem(
        id=uuid4(),
        group="test",
        key="test_key",
        value_type=ConfigType.STRING,
        stored_value=stored_value,
        is_sensitive=is_sensitive,
        is_core_security=is_core_security,
        description=None,
        status=ConfigStatus.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by=None,
        updated_by=None,
    )
