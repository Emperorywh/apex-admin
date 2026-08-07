"""示例领域策略（SPEC §5.2）。

领域策略封装业务校验规则，不依赖应用层异常类型或基础设施。
校验失败时抛出标准 Python 异常（``ValueError``），由应用层
Use Case 转换为携带稳定错误码的 :class:`~app.errors.ParameterError`。
"""

from __future__ import annotations


class ExampleNamePolicy:
    """示例名称校验策略。

    封装名称的不变式规则：非空、长度不超过 100 字符。
    策略是纯函数式校验器，不持有状态。

    Usage::

        ExampleNamePolicy.validate("hello")  # 通过
        ExampleNamePolicy.validate("")       # 抛出 ValueError
    """

    #: 名称最大长度
    MAX_NAME_LENGTH: int = 100

    @staticmethod
    def validate(name: str) -> None:
        """校验名称是否符合领域规则。

        Args:
            name: 待校验的名称

        Raises:
            ValueError: 名称为空、空白或超过最大长度
        """
        if not name or not name.strip():
            raise ValueError("名称不能为空")
        if len(name) > ExampleNamePolicy.MAX_NAME_LENGTH:
            raise ValueError(f"名称长度不能超过 {ExampleNamePolicy.MAX_NAME_LENGTH} 个字符")
