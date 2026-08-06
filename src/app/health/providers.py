"""健康检查与连接池 provider 接口（SPEC §6.1、§6.2）。

定义数据库连接池生命周期管理和就绪探针的抽象接口。
TASK-004 将提供 :class:`DbPoolProvider` 的具体实现（SQLAlchemy AsyncEngine），
TASK-005 将提供 Alembic revision 一致性校验的 :class:`ReadinessProbe` 实现。

本任务只定义接口契约并通过 DI 接线，不包含具体实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class DbPoolProvider(ABC):
    """数据库连接池生命周期管理端口（SPEC §6.1、§8.1）。

    管理数据库连接池的初始化、释放和连通性检查。
    Lifespan 在启动时调用 :meth:`initialize`，关闭时调用 :meth:`dispose`。
    就绪检查通过 :meth:`check_connection` 验证数据库可用性。

    TASK-004 将提供基于 SQLAlchemy AsyncEngine 的具体实现。
    """

    @abstractmethod
    async def initialize(self) -> None:
        """初始化数据库连接池。

        启动时由 Lifespan 调用。数据库暂时不可用时不应抛出异常，
        只影响就绪检查结果（SPEC §6.1）。
        """

    @abstractmethod
    async def dispose(self) -> None:
        """释放数据库连接池及相关资源。

        关闭时由 Lifespan 调用，确保连接被正确归还。
        """

    @abstractmethod
    async def check_connection(self) -> bool:
        """检查数据库连接是否可用。

        返回 True 表示数据库可连通，False 表示不可用。
        就绪检查在每次请求时调用，恢复后无需重启即可重新就绪（SPEC §6.2）。
        """


class ReadinessProbe(ABC):
    """就绪探针端口（SPEC §6.2）。

    代表一个独立的就绪条件。就绪检查端点遍历所有已注册的探针，
    任一探针返回 False 时整体返回 HTTP 503。

    TASK-005 将提供 Alembic revision 一致性校验的探针实现。
    """

    @abstractmethod
    async def probe(self) -> bool:
        """执行就绪检查。

        返回 True 表示条件满足，False 表示不满足。
        """
