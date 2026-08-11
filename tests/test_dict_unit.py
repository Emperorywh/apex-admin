"""数据字典模块单元测试 — SPEC 17.1 / 17.2.

覆盖:
  - 领域实体与状态枚举。
  - 错误码注册与异常类型。
  - Schema 校验（extra=forbid、字段约束）。
  - 种子初始化器编码。
"""

from __future__ import annotations

import pytest

from app.modules.dict.definition import MODULE_DEFINITION
from app.modules.dict.errors import (
    DICT_ITEM_ALREADY_ACTIVE,
    DICT_ITEM_ALREADY_DISABLED,
    DICT_ITEM_DUPLICATE_VALUE,
    DICT_ITEM_NOT_FOUND,
    DICT_TYPE_ALREADY_ACTIVE,
    DICT_TYPE_ALREADY_DISABLED,
    DICT_TYPE_DISABLED,
    DICT_TYPE_DUPLICATE_CODE,
    DICT_TYPE_NOT_FOUND,
    DICT_TYPE_REFERENCED,
)
from app.modules.dict.initializers import DictSeedInitializer
from app.modules.dict.models import DictItemStatus, DictTypeStatus
from app.modules.dict.port import DictRepository, ReferenceRegistryPort
from app.modules.dict.schemas import (
    DictItemCreateRequest,
    DictItemUpdateRequest,
    DictTypeCreateRequest,
    DictTypeUpdateRequest,
)

# ═══════════════════════════════════════════════════════════════════════════════
# 领域实体与状态枚举
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDictModels:
    """领域实体与状态枚举测试 — SPEC 17.1 / 17.2."""

    def test_dict_type_status_values(self) -> None:
        """字典类型状态枚举值稳定。"""

        assert DictTypeStatus.ACTIVE == "active"
        assert DictTypeStatus.DISABLED == "disabled"

    def test_dict_item_status_values(self) -> None:
        """字典项状态枚举值稳定。"""

        assert DictItemStatus.ACTIVE == "active"
        assert DictItemStatus.DISABLED == "disabled"


# ═══════════════════════════════════════════════════════════════════════════════
# 错误码
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDictErrorCodes:
    """错误码与异常测试 — SPEC 10.2."""

    def test_all_error_codes_format(self) -> None:
        """全部错误码符合 <MODULE>.<REASON> 格式。"""

        for code in (
            DICT_TYPE_NOT_FOUND,
            DICT_TYPE_DUPLICATE_CODE,
            DICT_TYPE_ALREADY_DISABLED,
            DICT_TYPE_ALREADY_ACTIVE,
            DICT_TYPE_REFERENCED,
            DICT_TYPE_DISABLED,
            DICT_ITEM_NOT_FOUND,
            DICT_ITEM_DUPLICATE_VALUE,
            DICT_ITEM_ALREADY_DISABLED,
            DICT_ITEM_ALREADY_ACTIVE,
        ):
            assert code.startswith("DICT.")
            parts = code.split(".")
            assert len(parts) == 2
            assert parts[1].replace("_", "").isalnum()

    def test_duplicate_code_error_is_conflict(self) -> None:
        """字典编码冲突错误码稳定。"""

        from app.core.errors.codes import default_registry

        entry = default_registry.get(DICT_TYPE_DUPLICATE_CODE)
        assert entry is not None
        assert entry.http_status == 409

    def test_referenced_error_is_conflict(self) -> None:
        """被引用删除保护错误码稳定。"""

        from app.core.errors.codes import default_registry

        entry = default_registry.get(DICT_TYPE_REFERENCED)
        assert entry is not None
        assert entry.http_status == 409


# ═══════════════════════════════════════════════════════════════════════════════
# Schema 校验
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDictSchemas:
    """Schema 校验测试 — SPEC 9.2."""

    def test_create_type_rejects_extra_fields(self) -> None:
        """创建请求拒绝未知字段（extra=forbid）。"""

        with pytest.raises(Exception):  # noqa: PT011, B017
            DictTypeCreateRequest(
                code="x",
                name="X",
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_create_type_code_pattern(self) -> None:
        """字典编码格式约束：小写字母/数字/下划线。"""

        # 合法
        DictTypeCreateRequest(code="valid_code", name="有效")
        DictTypeCreateRequest(code="abc123", name="有效")

        # 非法
        with pytest.raises(Exception):  # noqa: PT011, B017
            DictTypeCreateRequest(code="UPPER", name="大写")
        with pytest.raises(Exception):  # noqa: PT011, B017
            DictTypeCreateRequest(code="has space", name="空格")

    def test_update_type_rejects_extra_fields(self) -> None:
        """更新请求拒绝未知字段。"""

        with pytest.raises(Exception):  # noqa: PT011, B017
            DictTypeUpdateRequest(
                name="X",
                code="should_not_be_here",  # type: ignore[call-arg]
            )

    def test_create_item_rejects_extra_fields(self) -> None:
        """字典项创建请求拒绝未知字段。"""

        with pytest.raises(Exception):  # noqa: PT011, B017
            DictItemCreateRequest(
                label="X",
                value="x",
                extra="bad",  # type: ignore[call-arg]
            )

    def test_create_item_defaults(self) -> None:
        """字典项创建默认值：sort_order=0, metadata={}."""

        req = DictItemCreateRequest(label="X", value="x")
        assert req.sort_order == 0
        assert req.metadata == {}

    def test_update_item_rejects_extra_fields(self) -> None:
        """字典项更新请求拒绝未知字段。"""

        with pytest.raises(Exception):  # noqa: PT011, B017
            DictItemUpdateRequest(
                label="X",
                value="x",
                sort_order=0,
                extra="bad",  # type: ignore[call-arg]
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Port 抽象性
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDictPorts:
    """Port 抽象接口测试。"""

    def test_dict_repository_is_abstract(self) -> None:
        """DictRepository 是抽象类不可实例化。"""

        with pytest.raises(TypeError):
            DictRepository()  # type: ignore[abstract]

    def test_reference_registry_port_is_abstract(self) -> None:
        """ReferenceRegistryPort 是抽象类不可实例化。"""

        with pytest.raises(TypeError):
            ReferenceRegistryPort()  # type: ignore[abstract]


# ═══════════════════════════════════════════════════════════════════════════════
# 种子初始化器
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g3
@pytest.mark.unit
class TestDictSeedInitializer:
    """种子初始化器测试 — SPEC 8.5."""

    def test_initializer_code_format(self) -> None:
        """初始化器编码格式合法。"""

        init = DictSeedInitializer()
        assert init.code == "DICT.SEED_DICT_TYPES"

    def test_module_definition_registers_initializer(self) -> None:
        """ModuleDefinition 注册了种子初始化器。"""

        codes = [i.code for i in MODULE_DEFINITION.initializers]
        assert "DICT.SEED_DICT_TYPES" in codes

    def test_module_definition_registers_reference_port(self) -> None:
        """ModuleDefinition 公开 ReferenceRegistryPort。"""

        assert ReferenceRegistryPort in MODULE_DEFINITION.application_ports
