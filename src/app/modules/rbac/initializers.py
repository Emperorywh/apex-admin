"""RBAC 幂等初始化器 — 内置角色 — SPEC 8.5 / 13.2 / 25.2.

SPEC 8.5:
  - 初始化器使用稳定自然键执行幂等 upsert。
  - 初始化过程可重复执行且不会创建重复数据。
  - 初始化器只能写入本模块拥有的数据。

SPEC 13.2: "系统内置角色具有明确保护规则"。
``super_admin`` 是系统内置超级管理员角色（SPEC 13.4），通过初始化器
以稳定编码 ``super_admin`` 作为自然键幂等创建。

初始化器使用 ``ON CONFLICT (code) DO UPDATE`` 实现幂等 upsert，
保证重复执行不产生重复角色。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.initialization.framework import Initializer
from app.core.security.authorization import SUPER_ADMIN_ROLE_CODE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── 内置角色定义 ────────────────────────────────────────────────────────────
#
# 每个内置角色通过稳定编码作为自然键。
# display_name 和 description 在 upsert 时更新，确保代码定义与数据库一致。

_BUILTIN_ROLES: tuple[dict[str, str | int | bool], ...] = (
    {
        "code": SUPER_ADMIN_ROLE_CODE,
        "display_name": "超级管理员",
        "description": "系统内置超级管理员角色，拥有全部权限（SPEC 13.4）",
        "sort_order": 0,
    },
)


class BuiltinRolesInitializer(Initializer):
    """内置角色幂等初始化器 — SPEC 8.5 / 13.2.

    以角色编码（稳定自然键）执行幂等 upsert，保证重复执行不产生重复角色。
    创建的内置角色 ``is_builtin=True``，不可删除或禁用（SPEC 13.2）。
    """

    @property
    def code(self) -> str:
        return "RBAC.SEED_BUILTIN_ROLES"

    async def initialize(self, session: AsyncSession) -> None:
        """对每个内置角色执行幂等 upsert.

        SPEC 8.5: 使用 ``ON CONFLICT (code) DO UPDATE`` 实现幂等 upsert，
        以角色编码（非显示名称）作为唯一判断依据。
        """

        from sqlalchemy import text

        for role in _BUILTIN_ROLES:
            await session.execute(
                text(
                    "INSERT INTO rbac_roles "
                    "(id, code, display_name, description, status, "
                    "is_builtin, sort_order, created_at, updated_at) "
                    "VALUES "
                    "(:id, :code, :display_name, :description, 'active', "
                    "TRUE, :sort_order, "
                    "NOW() AT TIME ZONE 'UTC', "
                    "NOW() AT TIME ZONE 'UTC') "
                    "ON CONFLICT (code) DO UPDATE SET "
                    "  display_name = EXCLUDED.display_name, "
                    "  description = EXCLUDED.description, "
                    "  sort_order = EXCLUDED.sort_order, "
                    "  is_builtin = TRUE, "
                    "  updated_at = NOW() AT TIME ZONE 'UTC'",
                ),
                {
                    "id": _generate_deterministic_id(role["code"]),
                    "code": role["code"],
                    "display_name": role["display_name"],
                    "description": role["description"],
                    "sort_order": role["sort_order"],
                },
            )


def _generate_deterministic_id(code: str | int | bool) -> str:
    """为内置角色生成确定性 UUID — 保证幂等插入不冲突.

    使用 code 作为种子的 UUID v5（命名空间 URL），
    确保每次执行产生相同的 UUID。
    第一次 ON CONFLICT 不会触发（因为 ID 也相同），
    后续重复执行命中 code 的唯一约束走 DO UPDATE。
    """

    from uuid import NAMESPACE_URL, uuid5

    assert isinstance(code, str)
    return str(uuid5(NAMESPACE_URL, f"apex:rbac:role:{code}"))
