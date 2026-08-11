"""字典种子初始化器 — SPEC 8.5 / 17.1 / 17.2.

SPEC 8.5:
  - 初始化器使用稳定自然键执行幂等 upsert。
  - 初始化过程可重复执行且不会创建重复数据。
  - 初始化器只能写入本模块拥有的数据。

SPEC 17.1 / 17.2: 字典模块提供基础系统字典种子初始化器。
与 TASK-027 的 ``admin sync-seeds`` 协同——初始化器提供种子数据定义，
``admin sync-seeds`` 命令调用初始化框架执行全部种子。

初始化器使用 ``ON CONFLICT (code) DO UPDATE`` 实现幂等 upsert，
保证重复执行不产生重复字典类型和字典项。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.initialization.framework import Initializer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── 种子字典类型 ────────────────────────────────────────────────────────────
#
# 每个种子字典类型通过稳定编码作为自然键。
# 字典项通过 dict_type_code + value 作为复合唯一键。

_SEED_DICT_TYPES: tuple[dict[str, str], ...] = (
    {
        "code": "gender",
        "name": "性别",
        "description": "用户性别字典（SPEC 17.1 / 17.2 种子）",
    },
    {
        "code": "user_status",
        "name": "用户状态",
        "description": "用户状态字典",
    },
)

_SEED_DICT_ITEMS: tuple[dict[str, str | int], ...] = (
    {"dict_type_code": "gender", "value": "male", "label": "男", "sort_order": 0},
    {"dict_type_code": "gender", "value": "female", "label": "女", "sort_order": 1},
    {
        "dict_type_code": "user_status",
        "value": "active",
        "label": "启用",
        "sort_order": 0,
    },
    {
        "dict_type_code": "user_status",
        "value": "disabled",
        "label": "禁用",
        "sort_order": 1,
    },
)


def _type_deterministic_id(code: str) -> str:
    """为种子字典类型生成确定性 UUID — 保证幂等插入."""

    from uuid import NAMESPACE_URL, uuid5

    return str(uuid5(NAMESPACE_URL, f"apex:dict:type:{code}"))


def _item_deterministic_id(dict_type_code: str, value: str) -> str:
    """为种子字典项生成确定性 UUID — 保证幂等插入."""

    from uuid import NAMESPACE_URL, uuid5

    return str(uuid5(NAMESPACE_URL, f"apex:dict:item:{dict_type_code}:{value}"))


class DictSeedInitializer(Initializer):
    """字典种子幂等初始化器 — SPEC 8.5 / 17.1 / 17.2.

    以字典编码（稳定自然键）执行幂等 upsert，保证重复执行不产生重复数据。
    与 TASK-027 的 ``admin sync-seeds`` 命令协同。
    """

    @property
    def code(self) -> str:
        return "DICT.SEED_DICT_TYPES"

    async def initialize(self, session: AsyncSession) -> None:
        """对每个种子字典类型和字典项执行幂等 upsert.

        SPEC 8.5: 使用 ``ON CONFLICT DO UPDATE`` 实现幂等 upsert，
        以稳定编码（非显示名称）作为唯一判断依据。
        """

        from sqlalchemy import text

        # 字典类型 upsert
        for dt in _SEED_DICT_TYPES:
            await session.execute(
                text(
                    "INSERT INTO dict_types "
                    "(id, code, name, description, status, "
                    "created_at, updated_at) "
                    "VALUES "
                    "(:id, :code, :name, :description, 'active', "
                    "NOW() AT TIME ZONE 'UTC', "
                    "NOW() AT TIME ZONE 'UTC') "
                    "ON CONFLICT (code) DO UPDATE SET "
                    "  name = EXCLUDED.name, "
                    "  description = EXCLUDED.description, "
                    "  updated_at = NOW() AT TIME ZONE 'UTC'",
                ),
                {
                    "id": _type_deterministic_id(dt["code"]),
                    "code": dt["code"],
                    "name": dt["name"],
                    "description": dt["description"],
                },
            )

        # 字典项 upsert
        for item in _SEED_DICT_ITEMS:
            dict_type_code = str(item["dict_type_code"])
            value = str(item["value"])
            # 查找 dict_type_id（外键）
            row = (
                await session.execute(
                    text("SELECT id FROM dict_types WHERE code = :code"),
                    {"code": dict_type_code},
                )
            ).fetchone()
            if row is None:
                continue
            dict_type_id = str(row[0])

            await session.execute(
                text(
                    "INSERT INTO dict_items "
                    "(id, dict_type_id, label, value, sort_order, "
                    "metadata_, description, status, "
                    "created_at, updated_at) "
                    "VALUES "
                    "(:id, :dict_type_id, :label, :value, :sort_order, "
                    "'{}'::jsonb, NULL, 'active', "
                    "NOW() AT TIME ZONE 'UTC', "
                    "NOW() AT TIME ZONE 'UTC') "
                    "ON CONFLICT (dict_type_id, value) DO UPDATE SET "
                    "  label = EXCLUDED.label, "
                    "  sort_order = EXCLUDED.sort_order, "
                    "  updated_at = NOW() AT TIME ZONE 'UTC'",
                ),
                {
                    "id": _item_deterministic_id(dict_type_code, value),
                    "dict_type_id": dict_type_id,
                    "label": item["label"],
                    "value": value,
                    "sort_order": item["sort_order"],
                },
            )
