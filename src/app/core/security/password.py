"""Argon2id 密码哈希服务与密码策略 — SPEC 12.1 / 23.2.

SPEC 12.1:
  - 密码固定使用 Argon2id 和 argon2-cffi。
  - Argon2id 参数固定为 memory_cost=65536 KiB、time_cost=3、parallelism=1，
    变更参数必须通过安全 ADR 和性能测试。
  - 登录成功时使用 check_needs_rehash 判断并在同一事务中升级旧参数哈希。

SPEC 23.2:
  - Argon2id 为每个密码生成独立随机盐，不单独维护应用层固定盐。
  - 密码最小长度为 12 个 Unicode 字符，最大长度为 128 个 Unicode 字符；
    不得静默截断。
  - 禁止记录和回显密码。

随机盐由 argon2-cffi 在每次 hash 调用时自动生成，本模块不维护应用层固定盐
（SPEC 23.2: "不单独维护应用层固定盐"）。
"""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type

# ── Argon2id 固定参数（SPEC 12.1）─────────────────────────────────────────
#
# 变更以下任意参数必须通过安全 ADR 并附性能测试。
# memory_cost 单位为 KiB，对应 SPEC 12.1 的 65536 KiB（64 MiB）。

ARGON2_MEMORY_COST: int = 65536
"""Argon2id memory_cost — 固定 65536 KiB（64 MiB），SPEC 12.1。"""

ARGON2_TIME_COST: int = 3
"""Argon2id time_cost — 固定 3 轮迭代，SPEC 12.1。"""

ARGON2_PARALLELISM: int = 1
"""Argon2id parallelism — 固定 1 线程，SPEC 12.1。"""

# ── 密码长度策略（SPEC 23.2）──────────────────────────────────────────────

PASSWORD_MIN_LENGTH: int = 12
"""密码最小长度 — 12 个 Unicode 字符，SPEC 23.2。"""

PASSWORD_MAX_LENGTH: int = 128
"""密码最大长度 — 128 个 Unicode 字符，SPEC 23.2。"""


class PasswordPolicyError(ValueError):
    """密码策略违规 — 不满足 SPEC 23.2 的长度要求。

    使用 ValueError 子类，与审计模块字段白名单校验一致。
    Use Case（TASK-013）捕获此异常后翻译为合适的 Application Error。
    """


def validate_password_length(password: str) -> None:
    """校验密码长度是否符合策略 — SPEC 23.2.

    SPEC 23.2: "密码最小长度为 12 个 Unicode 字符，最大长度为 128 个
    Unicode 字符；不得静默截断。"

    长度按 Unicode 字符（code point）计数，``len(str)`` 在 Python 中返回
    码点数量。不进行任何截断。

    参数:
        password: 待校验的明文密码。

    抛出:
        PasswordPolicyError: 长度低于 12 或超过 128 个 Unicode 字符。
    """

    length = len(password)
    if length < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(
            f"密码长度 {length} 个字符，不足最小要求 {PASSWORD_MIN_LENGTH} 个字符",
        )
    if length > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(
            f"密码长度 {length} 个字符，超过最大限制 {PASSWORD_MAX_LENGTH} 个字符",
        )


class Argon2Hasher:
    """Argon2id 密码哈希服务 — SPEC 12.1.

    参数固定遵循 SPEC 12.1（memory_cost=65536 KiB、time_cost=3、parallelism=1），
    变更需安全 ADR。每次 hash 调用由 argon2-cffi 自动生成独立随机盐，
    不维护应用层固定盐（SPEC 23.2）。

    使用方式::

        hasher = Argon2Hasher()
        hashed = hasher.hash("user_password")
        if hasher.verify(hashed, "submitted_password"):
            ...
        if hasher.needs_rehash(hashed):
            # 在同一事务中升级旧参数哈希
            new_hashed = hasher.hash(raw_password)
    """

    def __init__(self) -> None:
        """构造 Argon2id 哈希器 — 使用 SPEC 12.1 固定参数。"""

        self._hasher = _Argon2PasswordHasher(
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            type=Type.ID,
        )

    def hash(self, plain: str) -> str:
        """对明文密码生成 Argon2id 哈希 — SPEC 12.1.

        每次 hash 调用由 argon2-cffi 自动生成独立随机盐。
        返回 PHC 格式的哈希字符串（``$argon2id$...``）。

        参数:
            plain: 明文密码。

        返回:
            Argon2id PHC 格式哈希字符串，包含算法标识、参数、盐和哈希值。
        """

        return self._hasher.hash(plain)

    def verify(self, hash: str, plain: str) -> bool:
        """验证明文密码是否匹配已有哈希 — SPEC 12.1.

        密码不匹配时返回 False（不抛异常），便于调用方在登录流程中
        以统一方式处理认证失败。

        参数:
            hash: 已存储的 Argon2id PHC 格式哈希字符串。
            plain: 待验证的明文密码。

        返回:
            密码匹配时返回 True，不匹配时返回 False。
        """

        try:
            self._hasher.verify(hash, plain)
        except VerifyMismatchError:
            return False
        return True

    def needs_rehash(self, hash: str) -> bool:
        """判断哈希是否需要使用当前参数重新计算 — SPEC 12.1.

        SPEC 12.1: "登录成功时使用 check_needs_rehash 判断并在同一事务中
        升级旧参数哈希"。

        当哈希使用的参数（memory_cost / time_cost / parallelism）与当前
        固定参数不一致时返回 True，调用方应在同一事务中重新哈希。

        参数:
            hash: 已存储的 Argon2id PHC 格式哈希字符串。

        返回:
            参数与当前不一致时返回 True，一致时返回 False。
        """

        return self._hasher.check_needs_rehash(hash)


# ── 防枚举虚拟哈希常量 — SPEC 12.4 ──────────────────────────────────────────
#
# SPEC 12.4: "用户不存在时执行固定的 Argon2id 虚拟哈希校验，降低响应时间差
# 导致的账号枚举风险。"
#
# 此常量是一个合法的 Argon2id 哈希字符串，使用 SPEC 12.1 固定参数生成。
# 它对应一个无意义的内部常量，对任何真实密码 verify 都会返回 False，
# 但执行完整的 Argon2id 运算以确保响应时间与真实用户验证一致。
#
# 登录用例（TASK-013）在用户不存在时调用:
#   hasher.verify(DUMMY_PASSWORD_HASH, submitted_password)
# 这会消耗与真实验证相同的 CPU 时间，降低基于响应时间的账号枚举风险。

DUMMY_PASSWORD_HASH: str = (
    "$argon2id$v=19$m=65536,t=3,p=1$Yi3gsffRZrnotVKps3oKcw"
    "$X5GNlZbYGdBUJo3d5fsxWJPb8XUQXpqdv78aHsMRCnk"
)
"""防枚举虚拟哈希常量 — SPEC 12.4.

用户不存在时使用此常量执行 Argon2id 校验，确保响应时间与真实用户验证
一致，降低基于响应时间的账号枚举风险。对任何真实密码 verify 均返回 False。
"""
