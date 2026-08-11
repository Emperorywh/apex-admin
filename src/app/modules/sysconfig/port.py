"""系统配置 Repository Port — SPEC 5.2 / 5.6 / 16.1 / 16.2.

SPEC 5.2: "Repository、Unit of Work 由 Application 或 Domain 内层定义"。
SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前 Unit of Work
拥有的 AsyncSession 构造"。

Port 定义在内层（模块 Application），不依赖 SQLAlchemy 或任何 ORM 类型。
Infrastructure 层的 Adapter 实现此 Port。

SPEC 16.1:
  - ``get_by_group_key`` 查询配置项（分组+键），用于唯一性检查。
  - ``list_by_group`` / ``list_groups`` 支持按分组管理配置。

SPEC 16.2:
  - ``get_active_by_group_key`` 查询启用配置项（统一读取服务使用）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from app.modules.sysconfig.models import ConfigItem


class ConfigRepository(ABC):
    """系统配置 Repository Port — 数据访问抽象接口.

    SPEC 5.2: Port 由 Application 层定义，Infrastructure 层实现。
    Port 方法签名不包含 SQLAlchemy 类型。
    返回值为领域实体（``ConfigItem``），不是 ORM 模型。
    """

    @abstractmethod
    async def add(self, item: ConfigItem) -> None:
        """添加新配置项到当前事务."""

    @abstractmethod
    async def get_by_id(self, config_id: UUID) -> ConfigItem | None:
        """按 ID 查询配置项，返回领域实体或 None。"""

    @abstractmethod
    async def get_by_group_key(
        self,
        group: str,
        key: str,
    ) -> ConfigItem | None:
        """按分组+键查询配置项 — 唯一性检查用.

        SPEC 16.1: 配置键在分组内唯一。
        """

    @abstractmethod
    async def save(self, item: ConfigItem) -> None:
        """保存配置项变更到当前事务."""

    @abstractmethod
    async def list_items(
        self,
        *,
        group: str | None = None,
        include_disabled: bool = True,
    ) -> list[ConfigItem]:
        """查询配置项列表.

        参数:
            group:            按分组过滤（None 不过滤）。
            include_disabled: 是否包含禁用状态的配置项。
        """

    @abstractmethod
    async def list_groups(self) -> list[str]:
        """查询全部配置分组（去重）— SPEC 16.1 按分组管理."""

    @abstractmethod
    async def list_sensitive_items(self) -> list[ConfigItem]:
        """查询全部敏感配置项 — 密钥轮换 re-encrypt 用.

        返回所有标记为敏感的配置项（不限状态），供 re-encrypt 命令遍历重加密。
        """
