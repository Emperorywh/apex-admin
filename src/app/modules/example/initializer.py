"""示例初始化器 — SPEC 8.5.

SPEC 8.5:
  - 初始化框架由 G1 提供，各模块通过 ModuleDefinition 注册本模块初始化器。
  - 初始化器使用稳定自然键或稳定编码执行幂等 upsert，
    不得按显示名称判断重复。
  - 初始化过程可重复执行且不会创建重复数据。
  - 初始化器只能写入本模块拥有的数据。

示例模块不携带业务演示数据（nonGoals）。此初始化器演示幂等 upsert 模式：
以稳定自然键 ``__example_init__`` 作为唯一判断依据，执行
``INSERT ... ON CONFLICT DO NOTHING``，多次执行不产生重复行。
派生项目应将此初始化器替换为真实的模块初始化逻辑。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from app.core.initialization.framework import Initializer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 结构标记的自然键 — 非业务演示数据，标识模块初始化已完成。
_INIT_MARKER_NAME = "__example_init__"


class ExampleInitializer(Initializer):
    """示例模块幂等初始化器 — SPEC 8.5.

    演示幂等 upsert 模式：使用稳定自然键执行 ``ON CONFLICT DO NOTHING``，
    多次执行不产生重复行（SPEC 8.5: "初始化过程可重复执行且不会创建重复数据"）。

    派生项目应将此初始化器替换为真实的模块初始化逻辑
    （如创建默认配置项、基础角色等），或直接删除整个示例模块。
    """

    @property
    def code(self) -> str:
        """全局唯一的初始化器编码。"""

        return "EXAMPLE.INIT"

    async def initialize(self, session: AsyncSession) -> None:
        """执行幂等 upsert — 插入结构标记行.

        SPEC 8.5: "初始化器使用稳定自然键或稳定编码执行幂等 upsert，
        不得按显示名称判断重复"。

        使用 ``ON CONFLICT (name) DO NOTHING`` 确保幂等性，
        以自然键 ``__example_init__`` 作为唯一判断依据。
        """

        await session.execute(
            text(
                "INSERT INTO example_items (id, name, description, "
                "created_at, updated_at) "
                "VALUES ("
                "  :id, :name, :description, "
                "  NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC'"
                ") ON CONFLICT (name) DO NOTHING",
            ),
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": _INIT_MARKER_NAME,
                "description": "example module initialized",
            },
        )
