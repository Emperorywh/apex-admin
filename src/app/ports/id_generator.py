"""ID Generator Port — 标识生成抽象（SPEC §5.8）。

领域规则通过显式 ID Generator Port 获取标识，而非直接调用 uuid 或数据库序列。
测试中通过注入假实现控制标识生成，确保领域逻辑可重复验证。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IdGenerator(ABC):
    """标识生成端口（SPEC §5.8）。

    领域规则通过此接口获取全局唯一标识。
    实现可选择 UUID、雪花算法或其他策略，但对领域层透明。
    """

    @abstractmethod
    def new_id(self) -> str:
        """生成一个新的全局唯一标识。"""
