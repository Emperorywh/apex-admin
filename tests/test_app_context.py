"""用例上下文、Clock/IdGenerator Port 与 Request Context 测试 — SPEC 5.8.

覆盖:
  - UseCaseContext 不可变且包含 SPEC 5.8 规定的全部字段。
  - Clock Port 返回 UTC 时间。
  - IdGenerator Port 生成唯一 UUID。
  - request_id_var ContextVar 行为正确。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from app.application.context import UseCaseContext
from app.application.ports import Clock, IdGenerator, SystemClock, UuidGenerator
from app.core.request_context import request_id_var

# ── UseCaseContext ─────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_use_case_context_is_frozen() -> None:
    """UseCaseContext 为不可变 frozen dataclass。"""

    ctx = UseCaseContext(
        request_id="req-1",
        current_time=datetime.now(UTC),
    )
    assert ctx.__class__.__dataclass_params__.frozen is True


@pytest.mark.g1
@pytest.mark.unit
def test_use_case_context_cannot_mutate() -> None:
    """不可变对象修改属性时抛出 FrozenInstanceError。"""

    ctx = UseCaseContext(request_id="req-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.request_id = "req-2"  # type: ignore[misc]


@pytest.mark.g1
@pytest.mark.unit
def test_use_case_context_has_required_fields() -> None:
    """UseCaseContext 包含 SPEC 5.8 规定的五个字段。"""

    fields = {f.name for f in dataclasses.fields(UseCaseContext)}
    assert fields == {
        "request_id",
        "actor_id",
        "session_id",
        "current_time",
        "security_metadata",
    }


@pytest.mark.g1
@pytest.mark.unit
def test_use_case_context_defaults() -> None:
    """G1 阶段 Actor/Session 为 None，security_metadata 为空只读映射。"""

    ctx = UseCaseContext(request_id="req-1")
    assert ctx.actor_id is None
    assert ctx.session_id is None
    assert len(ctx.security_metadata) == 0


@pytest.mark.g1
@pytest.mark.unit
def test_use_case_context_with_values() -> None:
    """填充完整字段值后读取正确。"""

    now = datetime.now(UTC)
    ctx = UseCaseContext(
        request_id="req-1",
        actor_id="user-1",
        session_id="sess-1",
        current_time=now,
        security_metadata={"role": "admin"},
    )
    assert ctx.request_id == "req-1"
    assert ctx.actor_id == "user-1"
    assert ctx.session_id == "sess-1"
    assert ctx.current_time == now
    assert ctx.security_metadata["role"] == "admin"


# ── Clock Port ─────────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_clock_is_abstract() -> None:
    """Clock 为抽象类，不能直接实例化。"""

    assert Clock.__abstractmethods__ == frozenset({"now"})


@pytest.mark.g1
@pytest.mark.unit
def test_system_clock_returns_utc() -> None:
    """SystemClock 返回 UTC 时区时间。"""

    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is not None
    # UTC 偏移为 0
    assert now.utcoffset().total_seconds() == 0


@pytest.mark.g1
@pytest.mark.unit
def test_system_clock_is_clock_port() -> None:
    """SystemClock 是 Clock Port 的实现。"""

    assert isinstance(SystemClock(), Clock)


# ── IdGenerator Port ───────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_id_generator_is_abstract() -> None:
    """IdGenerator 为抽象类，不能直接实例化。"""

    assert IdGenerator.__abstractmethods__ == frozenset({"generate_id"})


@pytest.mark.g1
@pytest.mark.unit
def test_uuid_generator_generates_unique_ids() -> None:
    """UuidGenerator 生成互不相同的 UUID。"""

    gen = UuidGenerator()
    id1 = gen.generate_id()
    id2 = gen.generate_id()
    assert id1 != id2


@pytest.mark.g1
@pytest.mark.unit
def test_uuid_generator_is_id_generator_port() -> None:
    """UuidGenerator 是 IdGenerator Port 的实现。"""

    assert isinstance(UuidGenerator(), IdGenerator)


# ── Request Context ContextVar ─────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_request_id_var_default_empty() -> None:
    """request_id_var 默认值为空字符串。"""

    assert request_id_var.get() == ""


@pytest.mark.g1
@pytest.mark.unit
def test_request_id_var_set_and_reset() -> None:
    """request_id_var 可设置和重置。"""

    token = request_id_var.set("test-req-id")
    assert request_id_var.get() == "test-req-id"
    request_id_var.reset(token)
    assert request_id_var.get() == ""
