"""示例模块单元测试 — SPEC 30.2 / 28.1.

覆盖:
  - 领域实体不可变性。
  - 错误码格式与异常类。
  - Schema 校验（extra="forbid"、字段约束）。
  - ModuleDefinition 声明完整性。
  - 事件处理器与初始化器编码。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.example.definition import MODULE_DEFINITION
from app.modules.example.errors import (
    EXAMPLE_CONFLICT,
    EXAMPLE_NOT_FOUND,
    ExampleItemConflictError,
    ExampleItemNotFoundError,
)
from app.modules.example.events import ExampleItemCreated
from app.modules.example.handler import ExampleItemCreatedHandler
from app.modules.example.initializer import ExampleInitializer
from app.modules.example.models import ExampleItem
from app.modules.example.schemas import (
    ExampleItemCreateRequest,
    ExampleItemResponse,
    ExampleItemUpdateRequest,
)

# ── 领域实体 ─────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_example_item_is_frozen() -> None:
    """领域实体不可变（SPEC 5.7: Domain Event 是不可变对象）。"""

    item = ExampleItem(
        id=uuid4(),
        name="test",
        description=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(FrozenInstanceError):
        item.name = "changed"  # type: ignore[misc]


@pytest.mark.g1
@pytest.mark.unit
def test_example_item_fields() -> None:
    """领域实体字段完整性。"""

    item_id = uuid4()
    now = datetime.now(UTC)
    item = ExampleItem(
        id=item_id,
        name="demo",
        description="a description",
        created_at=now,
        updated_at=now,
    )
    assert item.id == item_id
    assert item.name == "demo"
    assert item.description == "a description"
    assert item.created_at == now
    assert item.updated_at == now


# ── 事件 ─────────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_example_item_created_event_is_frozen() -> None:
    """事件为不可变对象（SPEC 5.7）。"""

    event = ExampleItemCreated(
        code="EXAMPLE.ITEM_CREATED",
        payload={"item_id": "abc", "name": "test"},
        item_id="abc",
        name="test",
    )
    assert event.code == "EXAMPLE.ITEM_CREATED"
    assert event.item_id == "abc"
    with pytest.raises(FrozenInstanceError):
        event.item_id = "changed"  # type: ignore[misc]


@pytest.mark.g1
@pytest.mark.unit
def test_example_item_created_handler_codes() -> None:
    """事件处理器编码与事件编码正确声明。"""

    handler = ExampleItemCreatedHandler()
    assert handler.code == "EXAMPLE.MARK_CREATED"
    assert handler.event_code == "EXAMPLE.ITEM_CREATED"


# ── 错误码与异常 ──────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_error_codes_format() -> None:
    """错误码符合 ``<MODULE>.<REASON>`` 格式（SPEC 5.5）。"""

    assert EXAMPLE_NOT_FOUND == "EXAMPLE.NOT_FOUND"
    assert EXAMPLE_CONFLICT == "EXAMPLE.CONFLICT"


@pytest.mark.g1
@pytest.mark.unit
def test_error_codes_registered_in_registry() -> None:
    """错误码已注册到框架注册表（SPEC 10.2）。"""

    from app.core.errors.codes import default_registry

    not_found = default_registry.get(EXAMPLE_NOT_FOUND)
    assert not_found is not None
    assert not_found.http_status == 404

    conflict = default_registry.get(EXAMPLE_CONFLICT)
    assert conflict is not None
    assert conflict.http_status == 409


@pytest.mark.g1
@pytest.mark.unit
def test_example_not_found_error_inheritance() -> None:
    """异常类继承正确（SPEC 10.1）。"""

    from app.core.errors.exceptions import NotFoundError

    exc = ExampleItemNotFoundError("item not found")
    assert isinstance(exc, NotFoundError)
    assert exc.code == EXAMPLE_NOT_FOUND


@pytest.mark.g1
@pytest.mark.unit
def test_example_conflict_error_inheritance() -> None:
    """异常类继承正确（SPEC 10.1）。"""

    from app.core.errors.exceptions import ConflictError

    exc = ExampleItemConflictError("name conflict")
    assert isinstance(exc, ConflictError)
    assert exc.code == EXAMPLE_CONFLICT


# ── Schema 校验 ─────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_create_request_rejects_unknown_fields() -> None:
    """创建请求拒绝未知字段（SPEC 9.2: extra="forbid"）。"""

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExampleItemCreateRequest.model_validate(
            {"name": "test", "unknown_field": "value"},
        )


@pytest.mark.g1
@pytest.mark.unit
def test_create_request_validates_name_length() -> None:
    """名称长度约束（SPEC 9.2）。"""

    from pydantic import ValidationError

    # 空名称
    with pytest.raises(ValidationError):
        ExampleItemCreateRequest.model_validate({"name": ""})

    # 过长名称
    with pytest.raises(ValidationError):
        ExampleItemCreateRequest.model_validate({"name": "a" * 201})

    # 合法名称
    req = ExampleItemCreateRequest.model_validate({"name": "valid"})
    assert req.name == "valid"
    assert req.description is None


@pytest.mark.g1
@pytest.mark.unit
def test_update_request_rejects_unknown_fields() -> None:
    """更新请求拒绝未知字段（SPEC 9.2）。"""

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExampleItemUpdateRequest.model_validate(
            {"name": "test", "extra": True},
        )


@pytest.mark.g1
@pytest.mark.unit
def test_response_model_serialization() -> None:
    """响应模型序列化 — snake_case + ISO 8601（SPEC 9.3）。"""

    item_id = uuid4()
    now = datetime.now(UTC)
    resp = ExampleItemResponse(
        id=item_id,
        name="test",
        description="desc",
        created_at=now,
        updated_at=now,
    )
    data = resp.model_dump(mode="json")
    assert data["id"] == str(item_id)
    assert data["name"] == "test"
    assert "created_at" in data
    assert isinstance(data["created_at"], str)


# ── ModuleDefinition 完整性 ───────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_code() -> None:
    """模块编码正确。"""

    assert MODULE_DEFINITION.code == "example"


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_has_router() -> None:
    """模块声明了 Router。"""

    assert len(MODULE_DEFINITION.routers) == 1


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_permission_codes() -> None:
    """权限编码格式正确（SPEC 5.5: 小写三段或多段）。"""

    for perm in MODULE_DEFINITION.permission_codes:
        assert perm.startswith("example:item:")


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_error_codes() -> None:
    """错误码在 ModuleDefinition 中声明。"""

    assert EXAMPLE_NOT_FOUND in MODULE_DEFINITION.error_codes
    assert EXAMPLE_CONFLICT in MODULE_DEFINITION.error_codes


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_event_codes() -> None:
    """事件编码在 ModuleDefinition 中声明。"""

    assert "EXAMPLE.ITEM_CREATED" in MODULE_DEFINITION.event_codes


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_alembic_dir() -> None:
    """Alembic 迁移版本目录已声明。"""

    assert MODULE_DEFINITION.alembic_version_dir is not None
    assert "example/migrations" in MODULE_DEFINITION.alembic_version_dir


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_has_initializer() -> None:
    """初始化器已注册。"""

    assert len(MODULE_DEFINITION.initializers) == 1
    assert MODULE_DEFINITION.initializers[0].code == "EXAMPLE.INIT"


@pytest.mark.g1
@pytest.mark.unit
def test_module_definition_audit_actions() -> None:
    """审计动作已声明。"""

    actions = MODULE_DEFINITION.audit_actions
    assert "example.item.create" in actions
    assert "example.item.update" in actions
    assert "example.item.delete" in actions


@pytest.mark.g1
@pytest.mark.unit
def test_initializer_code() -> None:
    """初始化器编码正确。"""

    init = ExampleInitializer()
    assert init.code == "EXAMPLE.INIT"


# ── Composition Root 注册验证 ─────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_example_module_in_manifest() -> None:
    """示例模块在 Composition Root 模块清单中注册（SPEC 5.5）。"""

    from app.composition.modules import get_module_manifest

    manifest = get_module_manifest()
    codes = [m.code for m in manifest]
    assert "example" in codes


@pytest.mark.g1
@pytest.mark.unit
def test_example_module_version_locations() -> None:
    """示例模块的迁移版本目录在 MODULE_VERSION_LOCATIONS 中（SPEC 8.2）。"""

    from app.composition.modules import MODULE_VERSION_LOCATIONS

    assert len(MODULE_VERSION_LOCATIONS) >= 1
    assert any("example/migrations" in loc for loc in MODULE_VERSION_LOCATIONS)


# ── ORM 模型 ─────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_orm_model_tablename() -> None:
    """ORM 表名正确。"""

    from app.modules.example.orm import ExampleItemORM

    assert ExampleItemORM.__tablename__ == "example_items"
