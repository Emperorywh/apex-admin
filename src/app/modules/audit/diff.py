"""变更差异字段白名单 — SPEC 18.2.

SPEC 18.2:
  - 审计差异使用字段白名单生成，禁止对任意对象执行反射式全字段序列化。
  - 密码、Token、密钥等敏感字段不得进入差异内容。

此模块提供:
  - ``FieldWhitelist``: 显式声明的允许字段集合，构造时拒绝敏感字段名。
  - ``generate_diff``: 根据白名单从前/后状态生成 ``ChangeDiff``。

设计原则:
  1. 白名单是唯一权威来源 — 未声明的字段永远不会出现在差异中。
  2. 敏感字段在白名单构造时被拒绝（"即使误声明也被拒绝"）。
  3. 生成差异时对值执行二次敏感检查（防御性掩码）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.audit.models import ChangeDiff, DiffField

if TYPE_CHECKING:
    from collections.abc import Mapping

# ── 敏感字段名片段 ──────────────────────────────────────────────────────────
#
# SPEC 18.2: "密码、Token、密钥等敏感字段不得进入差异内容"。
# 通过子串匹配（大小写不敏感）识别敏感字段名，
# 覆盖各种命名风格（snake_case、camelCase、kebab-case）。

_SENSITIVE_FIELD_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "credential",
    "apikey",
    "private_key",
)

# 敏感字段值掩码 — 统一占位符，不泄漏长度信息。
_MASKED_VALUE = "***MASKED***"


def _is_sensitive_field_name(field_name: str) -> bool:
    """判断字段名是否属于敏感类别.

    SPEC 18.2: 密码、Token、密钥等敏感字段不得进入差异内容。
    使用子串匹配（大小写不敏感）覆盖各种命名风格。
    """

    name_lower = field_name.lower()
    return any(fragment in name_lower for fragment in _SENSITIVE_FIELD_FRAGMENTS)


class FieldWhitelist:
    """字段白名单 — 显式声明允许进入审计差异的字段（SPEC 18.2）.

    SPEC 18.2: "审计差异使用字段白名单生成，禁止对任意对象执行
    反射式全字段序列化"。

    白名单是唯一权威来源 — 未声明的字段永远不会出现在差异中。
    敏感字段名在构造时被拒绝（即使误声明也被拒绝）。

    使用方式::

        whitelist = FieldWhitelist("user", "user", {"name", "email", "status"})
        diff = generate_diff(whitelist, before_state, after_state)
    """

    def __init__(
        self,
        module: str,
        resource_type: str,
        fields: frozenset[str],
    ) -> None:
        """构造字段白名单.

        参数:
            module:        模块编码（用于错误信息定位）。
            resource_type: 资源类型（用于错误信息定位）。
            fields:        允许的字段名集合。

        抛出:
            ValueError: 字段名中包含敏感字段名（即使误声明也被拒绝）。
        """

        self._module: str = module
        self._resource_type: str = resource_type

        # 校验每个字段名，拒绝敏感字段名。
        # SPEC 18.2: "密码、Token、密钥等敏感字段不得进入差异内容"。
        for field_name in fields:
            if _is_sensitive_field_name(field_name):
                raise ValueError(
                    f"字段白名单拒绝敏感字段: {field_name!r}，"
                    f"模块 {module} 资源 {resource_type}"
                    f"（SPEC 18.2: 敏感字段不得进入差异内容）",
                )

        self._fields: frozenset[str] = frozenset(fields)

    @property
    def module(self) -> str:
        """返回模块编码。"""

        return self._module

    @property
    def resource_type(self) -> str:
        """返回资源类型。"""

        return self._resource_type

    @property
    def fields(self) -> frozenset[str]:
        """返回允许的字段名集合。"""

        return self._fields

    def allows(self, field_name: str) -> bool:
        """判断字段名是否在白名单中。"""

        return field_name in self._fields

    def __contains__(self, field_name: str) -> bool:
        """支持 ``in`` 运算符。"""

        return field_name in self._fields


def generate_diff(
    whitelist: FieldWhitelist,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> ChangeDiff:
    """根据白名单从前/后状态生成变更差异 — SPEC 18.2.

    SPEC 18.2: "审计差异使用字段白名单生成，禁止对任意对象执行
    反射式全字段序列化"。

    只比较白名单中的字段。对于每个白名单字段:
      - 前/后值都存在且相等 → 不产生差异。
      - 前/后值不同 → 产生 ``DiffField``。
      - 字段不存在于前/后映射时视为 None。

    防御性掩码: 即使白名单构造时遗漏了敏感字段（理论上不可能，
    因为构造时会拒绝），生成差异时仍检查字段名并掩码值。

    参数:
        whitelist: 字段白名单。
        before:    变更前状态（字段名 → 值），可为 None。
        after:     变更后状态（字段名 → 值），可为 None。

    返回:
        ``ChangeDiff`` 对象，包含通过白名单过滤的字段差异。
    """

    before_map: Mapping[str, Any] = before or {}
    after_map: Mapping[str, Any] = after or {}

    diff_fields: list[DiffField] = []

    for field_name in sorted(whitelist.fields):
        old_value = before_map.get(field_name)
        new_value = after_map.get(field_name)

        # 值相同则无差异
        if old_value == new_value:
            continue

        # 防御性二次检查：即使白名单漏检，也掩码敏感字段值。
        # 正常流程下白名单构造时已拒绝敏感字段名，此处是纵深防御。
        if _is_sensitive_field_name(field_name):
            diff_fields.append(
                DiffField(
                    field_name=field_name,
                    old_value=_MASKED_VALUE,
                    new_value=_MASKED_VALUE,
                ),
            )
        else:
            diff_fields.append(
                DiffField(
                    field_name=field_name,
                    old_value=old_value,
                    new_value=new_value,
                ),
            )

    return ChangeDiff(fields=tuple(diff_fields))
