"""登录安全领域模型——暴力破解防护（SPEC §12.4）。

包含暴力破解防护的维度枚举、阈值常量和登录失败记录实体。

暴力破解防护基于 PostgreSQL 持久化，以两个独立维度统计连续失败
（SPEC §12.4：登录失败状态持久化到 PostgreSQL 以跨多 Worker 工作）：

- 账号维度：同一账号连续失败 ``ACCOUNT_LOCK_THRESHOLD`` 次后限制
  ``LOCK_DURATION_MINUTES`` 分钟；成功登录后清理该维度失败状态。
- IP 维度：同一可信客户端 IP 连续失败 ``IP_LOCK_THRESHOLD`` 次后限制
  ``LOCK_DURATION_MINUTES`` 分钟；到期自动解除；成功登录不清理。

任一维度触发限制时的响应与账号密码错误响应保持一致（SPEC §12.4）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

# ---------------------------------------------------------------------------
# 阈值常量（SPEC §12.4）
# ---------------------------------------------------------------------------

#: 账号维度连续失败阈值——5 次（SPEC §12.4）
ACCOUNT_LOCK_THRESHOLD: int = 5

#: IP 维度连续失败阈值——20 次（SPEC §12.4）
IP_LOCK_THRESHOLD: int = 20

#: 限制持续时间——15 分钟（SPEC §12.4）
LOCK_DURATION_MINUTES: int = 15


class LoginAttemptDimension(enum.StrEnum):
    """登录失败统计维度（SPEC §12.4、§8.3）。

    使用 ``StrEnum`` 确保数据库存储的稳定编码一致性（SPEC §8.3）。

    Attributes:
        ACCOUNT: 账号维度——以规范化账号标识统计
        IP: IP 维度——以可信客户端 IP 统计
    """

    ACCOUNT = "account"
    IP = "ip"


@dataclass(frozen=True)
class LoginAttempt:
    """登录失败记录实体（SPEC §12.4）。

    以维度（账号 / IP）和标识符为主键，统计连续失败次数和限制状态。
    实体不可变（frozen dataclass），修改操作通过方法返回新实例。

    限制语义：
    - ``locked_until`` 不为 None 且晚于当前时间 → 处于限制状态。
    - 限制到期后自动解除——下一次记录失败时重置计数为 1（SPEC §12.4：到期自动解除）。

    Attributes:
        dimension: 统计维度
        identifier: 标识符（规范化账号名或可信客户端 IP）
        failure_count: 连续失败次数
        locked_until: 限制截止时间（None 表示未限制）
        last_failure_at: 最近失败时间
    """

    dimension: LoginAttemptDimension
    identifier: str
    failure_count: int
    locked_until: datetime | None
    last_failure_at: datetime

    @classmethod
    def first_failure(
        cls,
        *,
        dimension: LoginAttemptDimension,
        identifier: str,
        current_time: datetime,
    ) -> LoginAttempt:
        """创建首次失败记录（计数为 1）。"""
        return cls(
            dimension=dimension,
            identifier=identifier,
            failure_count=1,
            locked_until=None,
            last_failure_at=current_time,
        )

    def is_locked(self, *, current_time: datetime | None = None) -> bool:
        """是否处于限制状态（SPEC §12.4）。

        ``locked_until`` 不为 None 且严格晚于当前时间时返回 True。
        限制已到期（``locked_until <= now``）时返回 False（自动解除）。
        """
        now = current_time or datetime.now(UTC)
        return self.locked_until is not None and now < self.locked_until

    def increment_failure(
        self,
        *,
        threshold: int,
        current_time: datetime,
    ) -> LoginAttempt:
        """返回递增失败次数后的新实例。

        如果之前的限制已过期（``locked_until <= now``），重置计数为 1
        （连续失败的定义——限制到期后计数归零，SPEC §12.4）。
        达到阈值时设置 ``locked_until``（当前时间 + LOCK_DURATION_MINUTES）。

        Args:
            threshold: 该维度的锁定阈值
            current_time: 当前 UTC 时间

        Returns:
            更新后的新 :class:`LoginAttempt` 实例
        """
        # 之前的限制已到期 → 重置计数（SPEC §12.4：到期自动解除）
        if self.locked_until is not None and current_time >= self.locked_until:
            new_count = 1
        else:
            new_count = self.failure_count + 1

        new_locked_until: datetime | None = None
        if new_count >= threshold:
            new_locked_until = current_time + timedelta(minutes=LOCK_DURATION_MINUTES)

        return replace(
            self,
            failure_count=new_count,
            locked_until=new_locked_until,
            last_failure_at=current_time,
        )
