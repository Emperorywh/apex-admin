"""审计变更差异生成（SPEC §18.2、§5.7）。

使用字段白名单生成审计差异，禁止对任意对象执行反射式全字段序列化
（SPEC §18.2：审计差异使用字段白名单生成）。

密码、Token、密钥等敏感字段绝不进入差异内容
（SPEC §18.2、§23.2：敏感字段不得进入差异内容）。

差异值类型（:class:`AuditDiff`、:class:`FieldChange`）定义在
``app.ports.audit`` 端口层（供跨模块审计端口引用），此处导入并
提供差异生成函数 :func:`compute_diff`。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.ports.audit import AuditDiff, FieldChange

#: 敏感字段名称集合（SPEC §18.2、§23.2）。
#:
#: 密码、Token、密钥、验证码等敏感字段绝不进入审计差异。
#: 字段名匹配不区分大小写——调用方传入任意大小写混合的字段名
#: 均会被过滤（防御性设计）。
SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "key",
        "secret",
        "authorization",
        "verification_code",
        "captcha",
        "code",
    }
)


def _is_sensitive(field_name: str) -> bool:
    """检查字段名是否为敏感字段（不区分大小写匹配）。

    Args:
        field_name: 字段名称

    Returns:
        为敏感字段时返回 True
    """
    return field_name.lower() in SENSITIVE_FIELDS


def _stringify(value: object) -> str | None:
    """将值序列化为字符串（None 保持为 None）。

    Args:
        value: 原始值

    Returns:
        字符串表示；None 返回 None
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list, tuple, set)):
        # 复杂容器使用 repr 简要表示，不递归序列化（安全限制）
        return repr(value)
    return str(value)


def compute_diff(
    *,
    before: Mapping[str, object],
    after: Mapping[str, object],
    allowed_fields: tuple[str, ...],
    sensitive_fields: frozenset[str] | None = None,
) -> AuditDiff:
    """使用字段白名单生成审计差异（SPEC §18.2）。

    仅比较 ``allowed_fields`` 中声明的字段，并排除敏感字段。
    敏感字段双重保障：即使敏感字段误入白名单，也会被过滤。

    Args:
        before: 变更前字段值映射
        after: 变更后字段值映射
        allowed_fields: 允许进入差异的字段白名单
        sensitive_fields: 额外敏感字段集合（合并到默认集合）；
            为 None 时仅使用默认 :data:`SENSITIVE_FIELDS`

    Returns:
        :class:`AuditDiff`，仅包含白名单中实际变化且非敏感的字段

    Raises:
        ValueError: 白名单为空（白名单禁止为空——禁止反射式全字段序列化）
    """
    if len(allowed_fields) == 0:
        raise ValueError("审计差异白名单不得为空——禁止反射式全字段序列化（SPEC §18.2）")

    effective_sensitive = SENSITIVE_FIELDS
    if sensitive_fields is not None:
        effective_sensitive = effective_sensitive | sensitive_fields

    changes: list[FieldChange] = []
    for field_name in allowed_fields:
        # 敏感字段永不进入差异——双重保障
        if _is_sensitive(field_name) or field_name.lower() in effective_sensitive:
            continue
        old_value = before.get(field_name)
        new_value = after.get(field_name)
        if old_value != new_value:
            changes.append(
                FieldChange(
                    field=field_name,
                    old=_stringify(old_value),
                    new=_stringify(new_value),
                )
            )

    return AuditDiff(changes=tuple(changes))
