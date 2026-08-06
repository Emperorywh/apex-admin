"""请求用例上下文（SPEC §5.8、§9.5）。

Router 将认证结果转换为不可变 :class:`UseCaseContext`，显式传给 Use Case。
UseCaseContext 只包含 Request ID、Actor ID、Session ID、当前时间和已验证的安全元数据。

ContextVar 只允许用于日志关联，不得作为业务授权、事务或领域状态的数据源（SPEC §5.8）。
业务上下文通过 UseCaseContext 显式传递，不依赖难以推导的隐式全局状态。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType


@dataclass(frozen=True)
class UseCaseContext:
    """用例上下文（SPEC §5.8）。

    不可变对象，由 Router 在认证完成后构造并显式传递给 Use Case。
    包含请求执行所需的最小安全上下文信息。

    属性:
        request_id: 请求唯一标识，用于日志关联和审计追踪
        actor_id: 操作者标识；未认证请求为 None
        session_id: 会话标识；未认证或无会话请求为 None
        current_time: 请求处理的当前 UTC 时间
        security_metadata: 已验证的安全元数据（只读映射），例如客户端 IP、User-Agent 摘要
    """

    request_id: str
    actor_id: str | None
    session_id: str | None
    current_time: datetime
    security_metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        """确保安全元数据不可变。

        frozen dataclass 禁止字段重新赋值，此处额外将传入的 Mapping 包装为
        MappingProxyType，防止调用方通过原始 dict 引用修改内容。
        """
        if not isinstance(self.security_metadata, MappingProxyType):
            object.__setattr__(
                self,
                "security_metadata",
                MappingProxyType(dict(self.security_metadata)),
            )
