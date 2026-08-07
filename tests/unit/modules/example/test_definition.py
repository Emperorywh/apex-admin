"""示例模块定义单元测试（SPEC §5.5、§30.2）。

验证模块定义的完整性：编码、端口、路由、权限点、错误码、事件、
事件处理器声明格式正确且全局唯一性约束在 ModuleRegistry 中通过。
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter

from app.modules.contract import (
    ErrorCode,
    EventHandlerDefinition,
    PermissionPoint,
)
from app.modules.example.application.port import ExampleApplicationPort
from app.modules.example.definition import MODULE
from app.modules.registry import ModuleRegistry

pytestmark = [pytest.mark.unit, pytest.mark.g1]


class TestModuleDefinition:
    """模块定义结构测试。"""

    def test_module_code_is_stable(self) -> None:
        """模块编码稳定。"""
        assert MODULE.code == "example"

    def test_application_port_is_example_port(self) -> None:
        """Application Port 指向示例端口类。"""
        assert MODULE.application_port is ExampleApplicationPort

    def test_has_router(self) -> None:
        """模块声明了至少一个 Router。"""
        assert len(MODULE.routers) >= 1
        for router in MODULE.routers:
            assert isinstance(router, APIRouter)

    def test_permission_points_format(self) -> None:
        """权限点编码使用小写三段格式（SPEC §5.5）。"""
        for perm in MODULE.permission_points:
            assert isinstance(perm, PermissionPoint)
            parts = perm.code.split(":")
            assert len(parts) >= 3, f"权限编码 {perm.code} 应至少三段"
            for part in parts:
                assert part == part.lower(), f"权限编码 {perm.code} 应全小写"

    def test_error_codes_format(self) -> None:
        """错误码使用 MODULE.REASON 格式（SPEC §5.5）。"""
        for err in MODULE.error_codes:
            assert isinstance(err, ErrorCode)
            assert err.code.startswith("EXAMPLE."), f"错误码 {err.code} 应以 EXAMPLE. 开头"

    def test_event_handlers_match_events(self) -> None:
        """每个事件处理器声明的事件编码在事件集合中存在。"""
        event_codes = {e.code for e in MODULE.events}
        for handler in MODULE.event_handlers:
            assert isinstance(handler, EventHandlerDefinition)
            assert handler.event_code in event_codes, (
                f"处理器 {handler.code} 引用的事件 {handler.event_code} 未在事件集合中声明"
            )

    def test_all_event_handlers_are_transactional(self) -> None:
        """G1 阶段所有事件处理器为事务内（SPEC §5.7）。"""
        for handler in MODULE.event_handlers:
            assert handler.transactional is True, f"处理器 {handler.code} 应为事务内处理器"

    def test_no_initializers(self) -> None:
        """示例模块不携带业务演示数据——无初始化器（SPEC §30.2）。"""
        assert len(MODULE.initializers) == 0

    def test_migration_version_dir_points_to_global_versions(self) -> None:
        """迁移版本目录指向全局 Alembic versions 目录。"""
        assert MODULE.migration_version_dir is not None
        assert MODULE.migration_version_dir.name == "versions"

    def test_registry_validates_without_conflicts(self) -> None:
        """模块注册表校验通过，无冲突（SPEC §5.5）。"""
        registry = ModuleRegistry([MODULE])
        assert registry.get_module("example") is MODULE

    def test_module_is_frozen(self) -> None:
        """ModuleDefinition 构造后不可变（SPEC §5.5）。"""
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            MODULE.code = "changed"  # type: ignore[misc]

    def test_api_tag_is_examples(self) -> None:
        """API 标签为 examples。"""
        assert MODULE.api_tag == "examples"
