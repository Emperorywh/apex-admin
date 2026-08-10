"""显式 Port — Clock 与 ID Generator（SPEC 5.8）.

领域规则通过显式 Clock Port 获取时间，通过显式 ID Generator Port
获取标识。Port 定义在 Application 层，具体实现可由 Composition Root
注入（SPEC 5.2: Port 由 Application 或 Domain 内层定义）。

将 Clock 和 ID Generator 抽象为 Port 而非直接调用 ``datetime.now()``
或 ``uuid4()``，使得领域规则的时间与标识来源可控、可测试。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import UUID, uuid4


class Clock(ABC):
    """时钟 Port — 领域规则通过此 Port 获取当前时间.

    SPEC 5.8: "领域规则通过显式 Clock Port 获取时间"。
    使用 Port 而非直接调用 ``datetime.now()`` 使得时间来源可控，
    便于测试中注入固定时间。
    """

    @abstractmethod
    def now(self) -> datetime:
        """返回当前 UTC 时间."""


class SystemClock(Clock):
    """系统时钟实现 — 基于系统时钟返回 UTC 时间.

    这是生产环境的默认实现。测试中可替换为返回固定时间的伪实现。
    """

    def now(self) -> datetime:
        """返回当前系统 UTC 时间."""

        return datetime.now(UTC)


class IdGenerator(ABC):
    """标识生成器 Port — 领域规则通过此 Port 获取唯一标识.

    SPEC 5.8: "通过显式 ID Generator Port 获取标识"。
    使用 Port 而非直接调用 ``uuid4()`` 使得标识生成方式可控，
    便于测试中注入确定性标识。
    """

    @abstractmethod
    def generate_id(self) -> UUID:
        """生成并返回一个新的唯一标识."""


class UuidGenerator(IdGenerator):
    """UUID v4 标识生成器实现.

    这是生产环境的默认实现，使用 ``uuid4`` 生成密码学安全的随机 UUID。
    """

    def generate_id(self) -> UUID:
        """生成并返回一个新的 UUID v4 标识."""

        return uuid4()
