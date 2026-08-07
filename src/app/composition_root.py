"""Composition Root — 显式模块清单与依赖装配（SPEC §5.2、§5.5）。

Composition Root 是唯一允许同时引用接口与具体实现并执行装配的位置
（SPEC §5.2）。

模块清单通过显式列表声明，禁止通过扫描包、导入副作用或命名约定
自动发现模块（SPEC §5.5、§32）。

新增模块只允许新增模块自身代码，并在此处的 ``ENABLED_MODULES``
列表中增加一项 :class:`~app.modules.contract.ModuleDefinition`；
不得修改核心模块内部实现（SPEC §5.5）。
"""

from __future__ import annotations

from app.modules.contract import ModuleDefinition
from app.modules.example.definition import MODULE as EXAMPLE_MODULE

# ---------------------------------------------------------------------------
# 显式模块清单（SPEC §5.5：无扫描、无导入副作用）
#
# 新增模块在此列表中增加一项 ModuleDefinition。
# 列表顺序决定初始化器执行顺序。
#
# G1 阶段：最小示例模块验证完整接入模式（SPEC §30.2）。
# G2 阶段：新增身份、认证、RBAC、审计等模块。
# G3 阶段：新增组织、菜单、配置、字典、文件等模块。
# ---------------------------------------------------------------------------
ENABLED_MODULES: list[ModuleDefinition] = [
    EXAMPLE_MODULE,
]


def get_enabled_modules() -> list[ModuleDefinition]:
    """返回显式声明的已启用模块清单。

    Composition Root 通过此函数提供模块清单，
    :class:`~app.modules.registry.ModuleRegistry` 在启动时校验此清单。

    Returns:
        模块定义列表的副本，调用方可安全修改
    """
    return list(ENABLED_MODULES)
