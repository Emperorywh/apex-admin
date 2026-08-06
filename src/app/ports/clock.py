"""Clock Port — 时间获取抽象（SPEC §5.8、§6.3）。

领域规则通过显式 Clock Port 获取当前时间，而非直接调用 ``datetime.now()``。
所有时间统一使用 UTC（SPEC §6.3），禁止使用无时区语义的时间参与关键业务计算。
测试中通过注入假实现控制时间，确保领域逻辑可重复验证。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class Clock(ABC):
    """时钟端口（SPEC §5.8）。

    领域规则通过此接口获取当前时间。实现必须返回携带 UTC 时区信息的 datetime，
    确保全局时间语义一致（SPEC §6.3）。
    """

    @abstractmethod
    def now(self) -> datetime:
        """返回当前 UTC 时间。

        返回的 datetime 必须携带 UTC 时区信息（``tzinfo=timezone.utc``）。
        """
