"""用例上下文 — SPEC 5.8.

Router 将认证结果转换为不可变 ``UseCaseContext``，显式传给 Use Case。
``UseCaseContext`` 只包含 Request ID、Actor ID、Session ID、当前时间
和已验证的安全元数据。

此对象为不可变（frozen dataclass），创建后不可修改。
认证填充由 G2 实现；G1 阶段 Actor ID 和 Session ID 为 None。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class UseCaseContext:
    """不可变用例上下文（SPEC 5.8）.

    每个字段都是显式传递的，不依赖 ContextVar、线程局部变量
    或全局状态。业务上下文显式传递，不依赖难以推导的隐式全局状态。

    属性:
        request_id:        请求唯一标识，用于日志关联和审计追踪。
        actor_id:          操作者标识；未认证时为 None（G1 阶段恒为 None，
                           G2 认证填充后携带用户 ID）。
        session_id:        会话标识；未认证时为 None（G1 阶段恒为 None，
                           G2 会话管理后携带会话 ID）。
        current_time:      当前时间（UTC），由 Clock Port 提供。
        security_metadata: 已验证的安全元数据（只读映射），由认证层填充。
                           G1 阶段为空映射。
    """

    request_id: str
    actor_id: str | None = None
    session_id: str | None = None
    current_time: datetime = field(
        default_factory=lambda: datetime.fromtimestamp(0),
    )
    security_metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
    )
