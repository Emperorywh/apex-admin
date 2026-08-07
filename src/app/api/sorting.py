"""排序参数解析与白名单校验（SPEC §9.4）。

提供可复用的排序参数解析工具，每个查询显式声明允许排序的字段白名单。

排序约定（SPEC §9.4）：
    - 排序参数固定为 ``sort``
    - 使用逗号分隔多个字段，例如 ``-created_at,name``
    - ``-`` 前缀表示降序，无前缀表示升序
    - 排序字段使用每个查询显式声明的白名单，不在白名单内返回参数错误
    - 禁止将客户端输入直接拼接为 SQL（SPEC §23.3）

解析结果为 :class:`SortInstruction` 列表，调用方根据指令构造 ORM order_by
或等效查询条件。本模块不依赖 ORM、SQL 或 FastAPI 请求对象，
便于在单元测试中独立验证解析和白名单逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.errors.base import ParameterError


@dataclass(frozen=True)
class SortInstruction:
    """单条排序指令（SPEC §9.4）。

    表示一个字段的排序方向，由 :func:`parse_sort` 解析产生。
    调用方根据 ``field`` 和 ``descending`` 构造具体的查询排序条件。

    Attributes:
        field: 排序字段名（已通过白名单校验）
        descending: 是否降序，True 为降序，False 为升序
    """

    field: str
    descending: bool


def parse_sort(
    sort: str | None,
    allowed_fields: frozenset[str],
) -> list[SortInstruction]:
    """解析排序参数并校验白名单（SPEC §9.4）。

    将逗号分隔的排序字符串解析为有序的 :class:`SortInstruction` 列表。
    每个字段必须在 ``allowed_fields`` 白名单中，否则抛出 :class:`ParameterError`。

    解析规则：
        - ``-created_at`` → 字段 ``created_at``，降序
        - ``name`` → 字段 ``name``，升序
        - ``-created_at,name`` → 两条指令，依次为降序和升序
        - 空字符串或 None → 返回空列表
        - 连续逗号或空白段被忽略

    Args:
        sort: 排序查询参数原始值，例如 ``"-created_at,name"``
        allowed_fields: 允许排序的字段白名单

    Returns:
        排序指令列表，保持客户端指定顺序；无排序参数时返回空列表

    Raises:
        ParameterError: 排序字段不在白名单中
    """
    if not sort:
        return []

    instructions: list[SortInstruction] = []

    for raw_segment in sort.split(","):
        segment = raw_segment.strip()
        if not segment:
            # 忽略连续逗号或空白段
            continue

        # ``-`` 前缀表示降序（SPEC §9.4）
        descending = segment.startswith("-")
        field = segment[1:] if descending else segment

        if field not in allowed_fields:
            raise ParameterError(
                f"排序字段不在允许范围内: {field}",
                code="APP.PARAMETER",
            )

        instructions.append(SortInstruction(field=field, descending=descending))

    return instructions
