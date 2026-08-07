"""示例领域实体（SPEC §5.2）。

``ExampleItem`` 是不可变领域实体，只包含业务标识和属性，
不依赖 ORM 模型或数据库细节。实体由领域工厂方法创建，
保证不变式（名称非空且长度合规）在构造时即满足。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ExampleItem:
    """示例领域实体。

    表达一个最小化的领域对象，演示实体从创建到持久化的完整流程。
    实体不可变（frozen dataclass），修改操作通过创建新实例完成。

    Attributes:
        id: 实体唯一标识（UUID）
        name: 名称，非空且不超过 100 字符
        created_at: 创建时间（UTC）
    """

    id: UUID
    name: str
    created_at: datetime

    @classmethod
    def new(cls, *, name: str, created_at: datetime) -> ExampleItem:
        """创建新实体，生成随机 UUID。

        Args:
            name: 名称（须通过领域策略校验）
            created_at: 创建时间（UTC）

        Returns:
            新的 :class:`ExampleItem` 实例
        """
        return cls(id=uuid4(), name=name, created_at=created_at)
