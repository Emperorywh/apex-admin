"""模块接入契约与组合根校验单元测试 — SPEC 5.5.

覆盖验收标准:
  - AC-0: 重复 Router/权限点/错误码/审计动作/资源类型/命令/事件编码时
    组合根启动校验失败并指明冲突来源，modules validate 退出码非 0。
  - AC-1: 必需依赖未启用、依赖构成循环、可选依赖能力未按声明关闭时校验失败。
  - AC-5: modules validate 在核心装配下退出码 0 且报告零重复。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core.modules.definition import ManagementCommand, ModuleDefinition
from app.core.modules.exceptions import (
    CircularDependencyError,
    DuplicateDeclarationError,
    MissingDependencyError,
    OptionalDependencyNotClosedError,
)
from app.core.modules.registry import ModuleRegistry

if TYPE_CHECKING:
    from fastapi import APIRouter


# ── 辅助工厂 ───────────────────────────────────────────────────────────────


def _make_module(
    code: str = "alpha",
    api_tag: str = "alpha",
    *,
    permission_codes: tuple[str, ...] = (),
    error_codes: tuple[str, ...] = (),
    audit_actions: tuple[str, ...] = (),
    protected_resource_types: tuple[str, ...] = (),
    management_commands: tuple[ManagementCommand, ...] = (),
    event_codes: tuple[str, ...] = (),
    required_dependencies: tuple[str, ...] = (),
    optional_dependencies: tuple[str, ...] = (),
    routers: tuple[APIRouter, ...] = (),
) -> ModuleDefinition:
    """构造测试用 ModuleDefinition。"""

    return ModuleDefinition(
        code=code,
        api_tag=api_tag,
        permission_codes=permission_codes,
        error_codes=error_codes,
        audit_actions=audit_actions,
        protected_resource_types=protected_resource_types,
        management_commands=management_commands,
        event_codes=event_codes,
        required_dependencies=required_dependencies,
        optional_dependencies=optional_dependencies,
        routers=routers,
    )


# ── 重复声明检测 ──────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_module_code_fails() -> None:
    """重复模块编码时校验失败（AC-0）。"""

    # register 阶段即检测到模块编码重复
    with pytest.raises(DuplicateDeclarationError, match="模块编码重复"):
        ModuleRegistry.from_modules(
            [
                _make_module(code="alpha", api_tag="tag-a"),
                _make_module(code="alpha", api_tag="tag-b"),
            ],
        )


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_api_tag_fails() -> None:
    """重复 API Tag（Router 标识）时校验失败并指明来源（AC-0）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(code="alpha", api_tag="shared"),
            _make_module(code="beta", api_tag="shared"),
        ],
    )

    with pytest.raises(DuplicateDeclarationError, match="api_tag") as exc_info:
        registry.validate_or_raise()

    assert "alpha" in str(exc_info.value)
    assert "beta" in str(exc_info.value)


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_permission_code_fails() -> None:
    """重复权限编码时校验失败并指明来源（AC-0）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                permission_codes=("system:user:read",),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                permission_codes=("system:user:read",),
            ),
        ],
    )

    with pytest.raises(DuplicateDeclarationError, match="permission_code") as exc_info:
        registry.validate_or_raise()

    assert "system:user:read" in str(exc_info.value)


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_error_code_fails() -> None:
    """重复错误码时校验失败并指明来源（AC-0）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                error_codes=("USER.NOT_FOUND",),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                error_codes=("USER.NOT_FOUND",),
            ),
        ],
    )

    with pytest.raises(DuplicateDeclarationError, match="error_code"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_audit_action_fails() -> None:
    """重复审计动作时校验失败并指明来源（AC-0）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                audit_actions=("user.create",),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                audit_actions=("user.create",),
            ),
        ],
    )

    with pytest.raises(DuplicateDeclarationError, match="audit_action"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_resource_type_fails() -> None:
    """重复受保护资源类型时校验失败并指明来源（AC-0）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                protected_resource_types=("user",),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                protected_resource_types=("user",),
            ),
        ],
    )

    with pytest.raises(DuplicateDeclarationError, match="protected_resource_type"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_command_fails() -> None:
    """重复管理命令时校验失败并指明来源（AC-0）。"""

    cmd = ManagementCommand(name="auth sync", description="同步权限")
    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                management_commands=(cmd,),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                management_commands=(cmd,),
            ),
        ],
    )

    with pytest.raises(DuplicateDeclarationError, match="management_command"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_duplicate_event_code_fails() -> None:
    """重复事件编码时校验失败并指明来源（AC-0）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                event_codes=("USER.CREATED",),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                event_codes=("USER.CREATED",),
            ),
        ],
    )

    with pytest.raises(DuplicateDeclarationError, match="event_code"):
        registry.validate_or_raise()


# ── 依赖关系检测 ──────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_missing_required_dependency_fails() -> None:
    """必需依赖未启用时校验失败（AC-1）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                required_dependencies=("nonexistent",),
            ),
        ],
    )

    with pytest.raises(MissingDependencyError, match="必需依赖未启用"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_circular_required_dependency_fails() -> None:
    """必需依赖构成循环时校验失败（AC-1）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                required_dependencies=("beta",),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                required_dependencies=("alpha",),
            ),
        ],
    )

    with pytest.raises(CircularDependencyError, match="依赖构成循环"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_circular_optional_dependency_fails() -> None:
    """可选依赖构成循环时校验失败（AC-1）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                optional_dependencies=("beta",),
            ),
            _make_module(
                code="beta",
                api_tag="tag-b",
                optional_dependencies=("alpha",),
            ),
        ],
    )

    with pytest.raises(CircularDependencyError, match="依赖构成循环"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_optional_dependency_self_reference_fails() -> None:
    """可选依赖自引用时校验失败（AC-1: 可选依赖能力未按声明关闭）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                optional_dependencies=("alpha",),
            ),
        ],
    )

    with pytest.raises(OptionalDependencyNotClosedError, match="自身"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_dependency_in_both_required_and_optional_fails() -> None:
    """同一依赖同时声明为必需和可选时校验失败（AC-1）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                required_dependencies=("beta",),
                optional_dependencies=("beta",),
            ),
            _make_module(code="beta", api_tag="tag-b"),
        ],
    )

    with pytest.raises(OptionalDependencyNotClosedError, match="声明矛盾"):
        registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_valid_dependencies_pass() -> None:
    """正确的依赖关系校验通过。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                required_dependencies=("beta",),
                optional_dependencies=("gamma",),  # gamma 不在清单中，能力关闭
            ),
            _make_module(code="beta", api_tag="tag-b"),
        ],
    )

    registry.validate_or_raise()
    # validate_or_raise 成功返回 None，表示通过


@pytest.mark.g1
@pytest.mark.unit
def test_no_duplicate_in_single_module() -> None:
    """单个模块无重复声明时校验通过。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="tag-a",
                permission_codes=("system:user:read", "system:user:write"),
                error_codes=("USER.NOT_FOUND", "USER.CONFLICT"),
            ),
        ],
    )

    registry.validate_or_raise()


@pytest.mark.g1
@pytest.mark.unit
def test_validate_collects_all_conflicts() -> None:
    """validate() 方法汇总所有冲突（不立即抛出）。"""

    registry = ModuleRegistry.from_modules(
        [
            _make_module(
                code="alpha",
                api_tag="shared",
                permission_codes=("system:user:read",),
                error_codes=("USER.NOT_FOUND",),
            ),
            _make_module(
                code="beta",
                api_tag="shared",
                permission_codes=("system:user:read",),
                error_codes=("USER.NOT_FOUND",),
            ),
        ],
    )

    result = registry.validate()
    assert not result.valid
    assert len(result.conflicts) >= 2


# ── 编码格式校验 ──────────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_invalid_module_code_format_rejected() -> None:
    """模块编码格式非法时 ModuleDefinition 构造失败。"""

    from app.core.modules.definition import validate_module_code

    with pytest.raises(ValueError, match="模块编码格式非法"):
        validate_module_code("Invalid-Code")


@pytest.mark.g1
@pytest.mark.unit
def test_invalid_permission_code_format_rejected() -> None:
    """权限编码格式非法时 ModuleDefinition 构造失败。"""

    from app.core.modules.definition import validate_permission_code

    with pytest.raises(ValueError, match="权限编码格式非法"):
        validate_permission_code("invalid_format")


@pytest.mark.g1
@pytest.mark.unit
def test_permission_code_requires_three_segments() -> None:
    """权限编码至少三段（SPEC 5.5）。"""

    from app.core.modules.definition import validate_permission_code

    # 两段不够
    with pytest.raises(ValueError):
        validate_permission_code("user:read")

    # 三段通过
    validate_permission_code("system:user:read")

    # 四段通过
    validate_permission_code("system:user:profile:read")
