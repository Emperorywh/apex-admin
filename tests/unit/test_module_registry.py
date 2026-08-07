"""模块注册表一致性测试（SPEC §5.5、§8.5）。

验证模块注册校验：
- 重复模块编码 / 路由 / 权限 / 错误码 / 审计动作 / 资源类型 / 事件 / 事件处理器 / 命令
- 必需依赖未启用 → 启动失败
- 依赖循环 → 启动失败
- 可选依赖未启用 → 能力关闭
- 注册表一致性（模块查询、初始化器收集）

SPEC §5.5：注册规则要求重复检测和依赖校验使启动和 CI 失败。
SPEC §8.5：初始化框架通过 ModuleDefinition 注册，执行幂等 upsert，可重复。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter

from app.modules.contract import (
    AuditAction,
    CommandDefinition,
    ErrorCode,
    EventDefinition,
    EventHandlerDefinition,
    Initializer,
    ModuleDefinition,
    PermissionPoint,
    ResourceType,
)
from app.modules.initialization import InitializationRunner
from app.modules.registry import ModuleRegistrationError, ModuleRegistry
from app.ports.unit_of_work import UnitOfWork

pytestmark = [pytest.mark.unit, pytest.mark.g1]


# ---------------------------------------------------------------------------
# 测试辅助：构造模块定义和依赖组件
# ---------------------------------------------------------------------------


class _FakePortA:
    """模块 A 的测试 Application Port。"""


class _FakePortB:
    """模块 B 的测试 Application Port。"""


class _FakePortC:
    """模块 C 的测试 Application Port。"""


def _make_router(get_path: str | None = None) -> APIRouter:
    """创建包含单个 GET 路由的 APIRouter。

    Args:
        get_path: 路由路径；为 None 时创建空路由器
    """
    router = APIRouter()

    if get_path is not None:

        async def _handler() -> dict[str, str]:
            return {"status": "ok"}

        router.add_api_route(get_path, _handler, methods=["GET"])

    return router


def _make_module(
    code: str,
    *,
    name: str | None = None,
    application_port: type = _FakePortA,
    api_tag: str | None = None,
    required_dependencies: frozenset[str] = frozenset(),
    optional_dependencies: frozenset[str] = frozenset(),
    routers: tuple[APIRouter, ...] = (),
    permission_points: frozenset[PermissionPoint] = frozenset(),
    error_codes: frozenset[ErrorCode] = frozenset(),
    audit_actions: frozenset[AuditAction] = frozenset(),
    resource_types: frozenset[ResourceType] = frozenset(),
    initializers: tuple[Initializer, ...] = (),
    events: frozenset[EventDefinition] = frozenset(),
    event_handlers: frozenset[EventHandlerDefinition] = frozenset(),
    commands: frozenset[CommandDefinition] = frozenset(),
    migration_version_dir: Path | None = None,
) -> ModuleDefinition:
    """构造测试用 ModuleDefinition，提供合理默认值。"""
    return ModuleDefinition(
        code=code,
        name=name or f"模块 {code}",
        description=f"测试模块 {code}",
        application_port=application_port,
        api_tag=api_tag or code,
        required_dependencies=required_dependencies,
        optional_dependencies=optional_dependencies,
        routers=routers,
        permission_points=permission_points,
        error_codes=error_codes,
        audit_actions=audit_actions,
        resource_types=resource_types,
        initializers=initializers,
        events=events,
        event_handlers=event_handlers,
        commands=commands,
        migration_version_dir=migration_version_dir,
    )


async def _noop_initializer(uow: UnitOfWork) -> None:
    """空操作初始化器，用于测试初始化框架调用。"""


class _FakeUow(UnitOfWork):
    """测试用 UnitOfWork，不连接数据库。"""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> _FakeUow:
        return self

    async def __aexit__(self, *args: object) -> None:
        if not self.committed:
            pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


# ---------------------------------------------------------------------------
# 重复检测测试
# ---------------------------------------------------------------------------


class TestDuplicateModuleCode:
    """重复模块编码检测（SPEC §5.5）。"""

    def test_duplicate_module_code_raises(self) -> None:
        """两个模块声明相同编码 → 启动失败。"""
        module_a = _make_module("USER", name="用户模块A")
        module_b = _make_module("USER", name="用户模块B")

        with pytest.raises(ModuleRegistrationError, match="重复模块编码"):
            ModuleRegistry([module_a, module_b])

    def test_unique_module_codes_succeed(self) -> None:
        """不同编码的模块注册成功。"""
        registry = ModuleRegistry([_make_module("A"), _make_module("B")])
        assert len(registry.modules) == 2


class TestDuplicateRoute:
    """重复路由检测（SPEC §5.5）。"""

    def test_duplicate_route_raises(self) -> None:
        """两个模块声明相同的 HTTP 路由 → 启动失败。"""
        router_a = _make_router("/items")
        router_b = _make_router("/items")
        module_a = _make_module("A", routers=(router_a,))
        module_b = _make_module("B", routers=(router_b,))

        with pytest.raises(ModuleRegistrationError, match="重复路由"):
            ModuleRegistry([module_a, module_b])

    def test_different_routes_succeed(self) -> None:
        """不同路由路径的模块注册成功。"""
        router_a = _make_router("/users")
        router_b = _make_router("/orgs")
        module_a = _make_module("A", routers=(router_a,))
        module_b = _make_module("B", routers=(router_b,))

        registry = ModuleRegistry([module_a, module_b])
        assert len(registry.modules) == 2


class TestDuplicatePermissionPoint:
    """重复权限点检测（SPEC §5.5）。"""

    def test_duplicate_permission_raises(self) -> None:
        """两个模块声明相同权限编码 → 启动失败。"""
        perm = PermissionPoint(code="system:user:read", description="读取用户")
        module_a = _make_module("A", permission_points=frozenset({perm}))
        module_b = _make_module("B", permission_points=frozenset({perm}))

        with pytest.raises(ModuleRegistrationError, match="重复权限点"):
            ModuleRegistry([module_a, module_b])

    def test_different_permissions_succeed(self) -> None:
        """不同权限编码的模块注册成功。"""
        perm_a = PermissionPoint(code="system:user:read", description="读取用户")
        perm_b = PermissionPoint(code="system:org:read", description="读取组织")
        module_a = _make_module("A", permission_points=frozenset({perm_a}))
        module_b = _make_module("B", permission_points=frozenset({perm_b}))

        registry = ModuleRegistry([module_a, module_b])
        assert len(registry.modules) == 2


class TestDuplicateErrorCode:
    """重复错误码检测（SPEC §5.5）。"""

    def test_duplicate_error_code_raises(self) -> None:
        """两个模块声明相同错误码 → 启动失败。"""
        err = ErrorCode(
            code="USER.NOT_FOUND",
            http_status=404,
            description="用户不存在",
        )
        module_a = _make_module("A", error_codes=frozenset({err}))
        module_b = _make_module("B", error_codes=frozenset({err}))

        with pytest.raises(ModuleRegistrationError, match="重复错误码"):
            ModuleRegistry([module_a, module_b])


class TestDuplicateAuditAction:
    """重复审计动作检测（SPEC §5.5）。"""

    def test_duplicate_audit_action_raises(self) -> None:
        """两个模块声明相同审计动作编码 → 启动失败。"""
        action = AuditAction(code="user.create", description="创建用户")
        module_a = _make_module("A", audit_actions=frozenset({action}))
        module_b = _make_module("B", audit_actions=frozenset({action}))

        with pytest.raises(ModuleRegistrationError, match="重复审计动作"):
            ModuleRegistry([module_a, module_b])


class TestDuplicateResourceType:
    """重复资源类型检测（SPEC §5.5）。"""

    def test_duplicate_resource_type_raises(self) -> None:
        """两个模块声明相同资源类型编码 → 启动失败。"""
        rt = ResourceType(code="user", description="用户资源")
        module_a = _make_module("A", resource_types=frozenset({rt}))
        module_b = _make_module("B", resource_types=frozenset({rt}))

        with pytest.raises(ModuleRegistrationError, match="重复资源类型"):
            ModuleRegistry([module_a, module_b])


class TestDuplicateEvent:
    """重复事件编码检测（SPEC §5.5、§5.7）。"""

    def test_duplicate_event_raises(self) -> None:
        """两个模块声明相同事件编码 → 启动失败。"""
        event = EventDefinition(code="user.created", description="用户创建事件")
        module_a = _make_module("A", events=frozenset({event}))
        module_b = _make_module("B", events=frozenset({event}))

        with pytest.raises(ModuleRegistrationError, match="重复事件"):
            ModuleRegistry([module_a, module_b])


class TestDuplicateEventHandler:
    """重复事件处理器编码检测（SPEC §5.5、§5.7）。"""

    def test_duplicate_event_handler_raises(self) -> None:
        """两个模块声明相同处理器编码 → 启动失败。"""
        handler = EventHandlerDefinition(
            code="audit.on_user_created",
            event_code="user.created",
            description="用户创建时审计",
        )
        module_a = _make_module("A", event_handlers=frozenset({handler}))
        module_b = _make_module("B", event_handlers=frozenset({handler}))

        with pytest.raises(ModuleRegistrationError, match="重复事件处理器"):
            ModuleRegistry([module_a, module_b])


class TestDuplicateCommand:
    """重复命令编码检测（SPEC §5.5）。"""

    def test_duplicate_command_raises(self) -> None:
        """两个模块声明相同命令编码 → 启动失败。"""
        cmd = CommandDefinition(code="sync-permissions", description="同步权限点")
        module_a = _make_module("A", commands=frozenset({cmd}))
        module_b = _make_module("B", commands=frozenset({cmd}))

        with pytest.raises(ModuleRegistrationError, match="重复命令"):
            ModuleRegistry([module_a, module_b])


# ---------------------------------------------------------------------------
# 依赖校验测试
# ---------------------------------------------------------------------------


class TestRequiredDependency:
    """必需依赖校验（SPEC §5.5）。"""

    def test_missing_required_dependency_raises(self) -> None:
        """必需依赖未启用 → 启动失败。"""
        module = _make_module("A", required_dependencies=frozenset({"B"}))

        with pytest.raises(ModuleRegistrationError, match="必需依赖未启用"):
            ModuleRegistry([module])

    def test_satisfied_required_dependency_succeeds(self) -> None:
        """必需依赖已启用 → 注册成功。"""
        module_a = _make_module("A", required_dependencies=frozenset({"B"}))
        module_b = _make_module("B")

        registry = ModuleRegistry([module_a, module_b])
        assert len(registry.modules) == 2


class TestDependencyCycle:
    """依赖循环检测（SPEC §5.5）。"""

    def test_two_module_cycle_raises(self) -> None:
        """A 依赖 B、B 依赖 A → 启动失败。"""
        module_a = _make_module("A", required_dependencies=frozenset({"B"}))
        module_b = _make_module("B", required_dependencies=frozenset({"A"}))

        with pytest.raises(ModuleRegistrationError, match="循环"):
            ModuleRegistry([module_a, module_b])

    def test_three_module_cycle_raises(self) -> None:
        """A → B → C → A 循环 → 启动失败。"""
        module_a = _make_module("A", required_dependencies=frozenset({"B"}))
        module_b = _make_module("B", required_dependencies=frozenset({"C"}))
        module_c = _make_module("C", required_dependencies=frozenset({"A"}))

        with pytest.raises(ModuleRegistrationError, match="循环"):
            ModuleRegistry([module_a, module_b, module_c])

    def test_optional_dependency_cycle_raises(self) -> None:
        """可选依赖也参与循环检测。"""
        module_a = _make_module("A", optional_dependencies=frozenset({"B"}))
        module_b = _make_module("B", optional_dependencies=frozenset({"A"}))

        with pytest.raises(ModuleRegistrationError, match="循环"):
            ModuleRegistry([module_a, module_b])

    def test_no_cycle_in_dag_succeeds(self) -> None:
        """有向无环依赖图 → 注册成功。"""
        module_a = _make_module("A", required_dependencies=frozenset({"B", "C"}))
        module_b = _make_module("B", required_dependencies=frozenset({"C"}))
        module_c = _make_module("C")

        registry = ModuleRegistry([module_a, module_b, module_c])
        assert len(registry.modules) == 3


class TestOptionalDependency:
    """可选依赖行为（SPEC §5.5）。"""

    def test_optional_dependency_not_enabled(self) -> None:
        """可选依赖未启用时注册成功，依赖标记为未满足。"""
        module = _make_module("A", optional_dependencies=frozenset({"B"}))

        registry = ModuleRegistry([module])
        assert not registry.is_dependency_satisfied("A", "B")
        unsatisfied = registry.get_unsatisfied_optional_dependencies("A")
        assert unsatisfied == frozenset({"B"})

    def test_optional_dependency_enabled(self) -> None:
        """可选依赖已启用时标记为满足。"""
        module_a = _make_module("A", optional_dependencies=frozenset({"B"}))
        module_b = _make_module("B")

        registry = ModuleRegistry([module_a, module_b])
        assert registry.is_dependency_satisfied("A", "B")
        assert registry.get_unsatisfied_optional_dependencies("A") == frozenset()

    def test_mixed_dependencies(self) -> None:
        """必需依赖满足、可选依赖未满足 → 注册成功。"""
        module_a = _make_module(
            "A",
            required_dependencies=frozenset({"B"}),
            optional_dependencies=frozenset({"C"}),
        )
        module_b = _make_module("B")

        registry = ModuleRegistry([module_a, module_b])
        assert registry.is_dependency_satisfied("A", "B")
        assert not registry.is_dependency_satisfied("A", "C")


# ---------------------------------------------------------------------------
# 注册表一致性测试
# ---------------------------------------------------------------------------


class TestRegistryConsistency:
    """注册表一致性验证（SPEC §5.5）。"""

    def test_empty_registry(self) -> None:
        """空模块清单 → 注册成功，无模块。"""
        registry = ModuleRegistry([])
        assert len(registry.modules) == 0

    def test_get_module_by_code(self) -> None:
        """按编码查询模块定义。"""
        module_a = _make_module("A", name="模块A")
        module_b = _make_module("B", name="模块B")

        registry = ModuleRegistry([module_a, module_b])
        assert registry.get_module("A") is module_a
        assert registry.get_module("B") is module_b
        assert registry.get_module("C") is None

    def test_module_order_preserved(self) -> None:
        """模块按注册顺序返回。"""
        module_a = _make_module("A")
        module_b = _make_module("B")
        module_c = _make_module("C")

        registry = ModuleRegistry([module_c, module_a, module_b])
        codes = [m.code for m in registry.modules]
        assert codes == ["C", "A", "B"]

    def test_registered_modules_have_correct_views(self) -> None:
        """RegisteredModule 包含正确的未满足可选依赖信息。"""
        module_a = _make_module(
            "A",
            optional_dependencies=frozenset({"B", "C"}),
        )
        module_b = _make_module("B")

        registry = ModuleRegistry([module_a, module_b])
        registered = registry.registered_modules

        assert len(registered) == 2
        # 模块 A 的可选依赖 B 已满足，C 未满足
        rm_a = next(rm for rm in registered if rm.definition.code == "A")
        assert rm_a.unsatisfied_optional_dependencies == frozenset({"C"})

    def test_full_module_definition_fields(self) -> None:
        """ModuleDefinition 含全部必需字段（SPEC §5.5 验收条件 #0）。"""
        perm = PermissionPoint(code="test:item:read", description="读取")
        error = ErrorCode(code="TEST.NOT_FOUND", http_status=404, description="不存在")
        audit = AuditAction(code="test.create", description="创建")
        resource = ResourceType(code="test_item", description="测试资源")
        event = EventDefinition(code="test.created", description="创建事件")
        handler = EventHandlerDefinition(
            code="test.on_created",
            event_code="test.created",
            description="创建处理器",
        )
        cmd = CommandDefinition(code="test-sync", description="同步")
        init = Initializer(code="test-init", description="初始化", run=_noop_initializer)
        router = _make_router("/test")

        module = ModuleDefinition(
            code="TEST",
            name="测试模块",
            description="完整字段测试",
            application_port=_FakePortA,
            api_tag="测试",
            required_dependencies=frozenset(),
            optional_dependencies=frozenset(),
            routers=(router,),
            permission_points=frozenset({perm}),
            error_codes=frozenset({error}),
            audit_actions=frozenset({audit}),
            resource_types=frozenset({resource}),
            initializers=(init,),
            events=frozenset({event}),
            event_handlers=frozenset({handler}),
            commands=frozenset({cmd}),
            migration_version_dir=Path("migrations/versions"),
        )

        registry = ModuleRegistry([module])
        stored = registry.get_module("TEST")

        assert stored is not None
        assert stored.code == "TEST"
        assert stored.application_port is _FakePortA
        assert len(stored.routers) == 1
        assert stored.permission_points == frozenset({perm})
        assert stored.error_codes == frozenset({error})
        assert stored.audit_actions == frozenset({audit})
        assert stored.resource_types == frozenset({resource})
        assert len(stored.initializers) == 1
        assert stored.events == frozenset({event})
        assert stored.event_handlers == frozenset({handler})
        assert stored.commands == frozenset({cmd})
        assert stored.migration_version_dir == Path("migrations/versions")


# ---------------------------------------------------------------------------
# 初始化框架测试（SPEC §8.5）
# ---------------------------------------------------------------------------


class TestInitializationRunner:
    """幂等初始化框架测试（SPEC §8.5）。"""

    async def test_run_all_executes_initializers_in_order(self) -> None:
        """初始化器按模块和声明顺序执行。"""
        call_log: list[str] = []

        async def init_a1(uow: UnitOfWork) -> None:
            call_log.append("A1")

        async def init_a2(uow: UnitOfWork) -> None:
            call_log.append("A2")

        async def init_b1(uow: UnitOfWork) -> None:
            call_log.append("B1")

        module_a = _make_module(
            "A",
            initializers=(
                Initializer(code="a1", description="Init A1", run=init_a1),
                Initializer(code="a2", description="Init A2", run=init_a2),
            ),
        )
        module_b = _make_module(
            "B",
            initializers=(Initializer(code="b1", description="Init B1", run=init_b1),),
        )

        registry = ModuleRegistry([module_a, module_b])
        runner = InitializationRunner(registry, _FakeUow)
        await runner.run_all()

        assert call_log == ["A1", "A2", "B1"]

    async def test_run_all_is_repeatable(self) -> None:
        """初始化框架可重复执行（SPEC §8.5）。"""
        call_count = 0

        async def init_counter(uow: UnitOfWork) -> None:
            nonlocal call_count
            call_count += 1

        module = _make_module(
            "A",
            initializers=(Initializer(code="init", description="Counter", run=init_counter),),
        )

        registry = ModuleRegistry([module])
        runner = InitializationRunner(registry, _FakeUow)

        # 首次执行
        await runner.run_all()
        assert call_count == 1

        # 重复执行——幂等性由初始化器自身保证
        await runner.run_all()
        assert call_count == 2

    async def test_run_all_no_initializers(self) -> None:
        """无初始化器时 run_all 正常完成。"""
        module = _make_module("A")
        registry = ModuleRegistry([module])
        runner = InitializationRunner(registry, _FakeUow)

        # 不抛出异常即通过
        await runner.run_all()

    async def test_run_all_uses_unit_of_work(self) -> None:
        """初始化器在 Unit of Work 中执行（SPEC §8.5）。"""
        received_uows: list[UnitOfWork] = []

        async def init(uow: UnitOfWork) -> None:
            received_uows.append(uow)

        module = _make_module(
            "A",
            initializers=(Initializer(code="init", description="Init", run=init),),
        )

        registry = ModuleRegistry([module])
        runner = InitializationRunner(registry, _FakeUow)
        await runner.run_all()

        assert len(received_uows) == 1
        assert isinstance(received_uows[0], _FakeUow)

    async def test_run_all_stops_on_failure(self) -> None:
        """初始化器失败时立即停止，后续不再执行。"""
        executed: list[str] = []

        async def init_ok(uow: UnitOfWork) -> None:
            executed.append("ok")

        async def init_fail(uow: UnitOfWork) -> None:
            executed.append("fail")
            raise RuntimeError("初始化失败")

        async def init_after(uow: UnitOfWork) -> None:
            executed.append("after")

        module = _make_module(
            "A",
            initializers=(
                Initializer(code="ok", description="OK", run=init_ok),
                Initializer(code="fail", description="Fail", run=init_fail),
                Initializer(code="after", description="After", run=init_after),
            ),
        )

        registry = ModuleRegistry([module])
        runner = InitializationRunner(registry, _FakeUow)

        with pytest.raises(RuntimeError, match="初始化失败"):
            await runner.run_all()

        # 第三个初始化器未执行
        assert executed == ["ok", "fail"]
