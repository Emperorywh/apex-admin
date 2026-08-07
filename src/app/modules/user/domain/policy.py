"""用户领域策略（SPEC §5.2、§23.2）。

领域策略封装用户名和密码的校验规则，不依赖应用层异常或基础设施。
校验失败时抛出标准 Python 异常（``ValueError``），由应用层 Use Case
转换为携带稳定错误码的 :class:`~app.errors.ParameterError`。
"""

from __future__ import annotations

import re


class UsernamePolicy:
    """用户名校验策略（SPEC §11.2）。

    用户名是全局唯一的登录账号标识，需满足格式约束：
    长度 3–50 个字符，只允许大小写字母、数字、下划线和连字符。

    策略是纯函数式校验器，不持有状态。
    """

    #: 用户名最小长度
    MIN_LENGTH: int = 3

    #: 用户名最大长度
    MAX_LENGTH: int = 50

    #: 允许的字符模式：大小写字母、数字、下划线、连字符
    PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]+$")

    @staticmethod
    def validate(username: str) -> None:
        """校验用户名是否符合领域规则。

        Args:
            username: 待校验的用户名

        Raises:
            ValueError: 用户名为空、长度不合规或包含非法字符
        """
        if not username or not username.strip():
            raise ValueError("用户名不能为空")
        if len(username) < UsernamePolicy.MIN_LENGTH:
            raise ValueError(f"用户名长度不能少于 {UsernamePolicy.MIN_LENGTH} 个字符")
        if len(username) > UsernamePolicy.MAX_LENGTH:
            raise ValueError(f"用户名长度不能超过 {UsernamePolicy.MAX_LENGTH} 个字符")
        if not UsernamePolicy.PATTERN.match(username):
            raise ValueError("用户名只能包含大小写字母、数字、下划线和连字符")


class PasswordPolicy:
    """密码校验策略（SPEC §23.2）。

    密码长度约束以 Unicode 字符计数（Python ``len`` 统计 Unicode 码点）：
    最小 12 个字符，最大 128 个字符。不得静默截断——超长密码直接拒绝，
    而非截断为最大长度（SPEC §23.2）。

    策略是纯函数式校验器，不持有状态。
    """

    #: 密码最小长度（Unicode 字符数）
    MIN_LENGTH: int = 12

    #: 密码最大长度（Unicode 字符数）
    MAX_LENGTH: int = 128

    @staticmethod
    def validate(password: str) -> None:
        """校验密码是否符合领域规则（SPEC §23.2）。

        长度以 Unicode 字符计数（``len(password)``）。
        过短或过长均抛出异常，不截断（SPEC §23.2：不得静默截断）。

        Args:
            password: 待校验的明文密码

        Raises:
            ValueError: 密码长度少于 12 或超过 128 个 Unicode 字符
        """
        char_count = len(password)
        if char_count < PasswordPolicy.MIN_LENGTH:
            raise ValueError(f"密码长度不能少于 {PasswordPolicy.MIN_LENGTH} 个字符")
        if char_count > PasswordPolicy.MAX_LENGTH:
            raise ValueError(f"密码长度不能超过 {PasswordPolicy.MAX_LENGTH} 个字符")
