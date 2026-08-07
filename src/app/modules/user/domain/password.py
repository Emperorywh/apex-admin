"""Argon2id 密码哈希服务（SPEC §12.1、§23.2）。

固定使用 Argon2id 算法和 argon2-cffi 库。参数遵循 SPEC §12.1 固定值：
``memory_cost=65536`` KiB、``time_cost=3``、``parallelism=1``。
变更参数必须通过安全 ADR 和性能测试（SPEC §12.1）。

Argon2id 为每个密码生成独立随机盐，不单独维护应用层固定盐
（SPEC §23.2）。``needs_rehash`` 用于检测旧参数哈希，
在认证模块（TASK-015）登录成功时升级（SPEC §12.1）。
"""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import VerifyMismatchError


class PasswordHasher:
    """Argon2id 密码哈希服务（SPEC §12.1）。

    封装 argon2-cffi 的 :class:`~argon2.PasswordHasher`，固定使用
    SPEC §12.1 定义的参数。每次哈希自动生成独立随机盐。

    此服务由应用层 Use Case 使用——创建用户、重置密码和自助改密时
    调用 :meth:`hash`；自助改密时调用 :meth:`verify` 校验当前密码。

    Attributes:
        MEMORY_COST: 内存成本（KiB），固定 65536（SPEC §12.1）
        TIME_COST: 时间成本（迭代次数），固定 3（SPEC §12.1）
        PARALLELISM: 并行度，固定 1（SPEC §12.1）
    """

    #: Argon2id 内存成本（KiB）——SPEC §12.1 固定参数
    MEMORY_COST: int = 65536

    #: Argon2id 时间成本（迭代次数）——SPEC §12.1 固定参数
    TIME_COST: int = 3

    #: Argon2id 并行度——SPEC §12.1 固定参数
    PARALLELISM: int = 1

    def __init__(self) -> None:
        self._hasher = _Argon2Hasher(
            memory_cost=self.MEMORY_COST,
            time_cost=self.TIME_COST,
            parallelism=self.PARALLELISM,
        )

    def hash(self, password: str) -> str:
        """对明文密码进行 Argon2id 哈希（SPEC §12.1、§23.2）。

        每次调用自动生成独立随机盐，返回值包含算法参数、盐和哈希结果，
        可直接存储在数据库 ``password_hash`` 列中。

        Args:
            password: 明文密码

        Returns:
            Argon2id 编码哈希字符串
        """
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """验证明文密码是否匹配哈希值（SPEC §12.1）。

        密码不匹配时返回 ``False``（不抛出异常），由调用方根据返回值
        判断认证结果。哈希格式损坏时抛出 argon2 异常（数据损坏信号）。

        Args:
            password_hash: 数据库中存储的 Argon2id 哈希字符串
            password: 待验证的明文密码

        Returns:
            匹配返回 ``True``，不匹配返回 ``False``
        """
        try:
            self._hasher.verify(password_hash, password)
            return True
        except VerifyMismatchError:
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """检测哈希是否需要使用当前参数重新哈希（SPEC §12.1）。

        认证模块（TASK-015）在登录成功时调用此方法，若返回 ``True``
        则在同一事务中升级密码哈希到当前参数。

        Args:
            password_hash: 数据库中存储的 Argon2id 哈希字符串

        Returns:
            需要重新哈希返回 ``True``
        """
        return self._hasher.check_needs_rehash(password_hash)
