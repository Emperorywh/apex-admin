"""Domain Event — 不可变领域事件（SPEC 5.7）.

SPEC 5.7:
  - Domain Event 是不依赖 FastAPI、ORM 和基础设施的不可变对象。
  - 跨模块事件载荷只允许稳定编码、标量值和资源 ID，
    不得携带 ORM 模型或可变领域对象。

事件是 frozen dataclass，创建后不可修改。每个事件携带全局唯一
的稳定事件编码 ``code``，分发器据此将事件路由到对应的处理器。

事件编码格式固定为 ``<MODULE>.<EVENT_NAME>``，仅大写字母、数字和
下划线，例如 ``USER.CREATED``。格式校验在模块注册表启动校验时完成
（SPEC 5.5: "重复事件编码或处理器编码必须使启动和 CI 失败"）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 事件编码格式：<MODULE>.<EVENT_NAME>，仅大写字母、数字和下划线。
# MODULE 和 EVENT_NAME 均以大写字母开头，可含大写字母、数字和下划线。
# 与错误码格式一致（SPEC 5.5: 错误码 <MODULE>.<REASON>）。
_EVENT_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$")


def validate_event_code(code: str) -> None:
    """校验事件编码格式.

    SPEC 5.7: 事件编码格式固定为 ``<MODULE>.<EVENT_NAME>``，
    仅大写字母、数字和下划线。

    参数:
        code: 待校验的事件编码。

    抛出:
        ValueError: 编码格式不合法。
    """

    if not isinstance(code, str) or not _EVENT_CODE_PATTERN.match(code):
        raise ValueError(
            f"事件编码格式非法: {code!r}，"
            f"应为 <MODULE>.<EVENT_NAME>，仅大写字母、数字和下划线",
        )


@dataclass(frozen=True)
class DomainEvent:
    """不可变领域事件基类 — SPEC 5.7.

    SPEC 5.7: "Domain Event 是不依赖 FastAPI、ORM 和基础设施的不可变对象"。

    属性:
        code: 全局唯一的稳定事件编码，格式 ``<MODULE>.<EVENT_NAME>``。
        payload: 事件载荷，只允许稳定编码、标量值和资源 ID
                 （SPEC 5.7: "跨模块事件载荷只允许稳定编码、标量值和资源 ID"）。
                 不允许携带 ORM 模型或可变领域对象。

    子类可以添加更具体的载荷字段，但必须保持 ``frozen=True`` 不可变性。

    使用方式::

        @dataclass(frozen=True)
        class UserCreated(DomainEvent):
            user_id: str = ""

    事件实例在 Use Case 执行过程中创建，通过 ``TransactionalEventDispatcher``
    在 UoW 提交前同步分发到对应处理器。
    """

    code: str
    payload: dict[str, Any] = field(default_factory=dict)
