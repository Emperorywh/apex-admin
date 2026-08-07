"""进程内轻量任务工具（SPEC §20.1）。

提供基于 ``asyncio.create_task`` 的进程内后台任务调度工具。

适用范围与限制（SPEC §20.1）：
    - **只用于**可丢失、可快速完成、失败不影响核心业务的数据处理
    - **禁止用于**必须重试的通知、导入导出和关键业务操作
    - **进程关闭时未完成任务可能丢失**——本工具不保证任务持久化或可靠送达
    - 任务异常必须记录日志

与持久化任务的区别（SPEC §20.2）：
    持久化任务（EXT 扩展）将任务状态持久化到 PostgreSQL，由独立 Worker 拉取执行，
    支持租约、重试和状态查询。本工具不提供上述能力。
    禁止使用本工具替代持久化任务执行不可丢失的操作（SPEC §32）。

多 Worker 注意事项（SPEC §5.3）：
    多 Worker 不共享 Python 进程内状态。每个 Worker 各自维护自己的任务集合，
    任务仅在所属进程内可见。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

_logger = logging.getLogger("app.tasks.background")


class InProcessTaskRunner:
    """进程内轻量任务调度器（SPEC §20.1）。

    在当前事件循环中使用 ``asyncio.create_task`` 调度协程。
    任务在后台异步执行，不阻塞请求处理。

    **进程关闭时未完成任务可能丢失**（SPEC §20.1）。
    本工具不持久化任务状态，进程意外终止时所有未完成任务都会丢失。
    只用于可丢失、可快速完成、失败不影响核心业务的数据处理。

    任务异常自动记录到结构化日志（SPEC §20.1），不会传播到调用方。

    用法::

        runner = InProcessTaskRunner()

        async def cleanup_cache(key: str) -> None:
            ...

        # 调度后台任务（fire-and-forget）
        runner.schedule(cleanup_cache("user:123"), name="cleanup-cache-user-123")

        # 优雅关闭时等待正在执行的任务
        await runner.shutdown()
    """

    def __init__(self) -> None:
        """初始化任务调度器。

        内部维护一个任务集合，强引用正在执行的 ``asyncio.Task``，
        防止任务被垃圾回收器回收。
        """
        self._tasks: set[asyncio.Task[Any]] = set()

    def schedule(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str = "",
    ) -> asyncio.Task[Any]:
        """调度一个协程在后台执行（SPEC §20.1）。

        调度后协程在当前事件循环中异步运行，不阻塞调用方。
        任务完成或异常后自动从内部集合移除。
        任务异常记录到结构化日志，不传播到调用方。

        **此方法只用于可丢失、可快速完成、失败不影响核心业务的任务**
        （SPEC §20.1）。禁止用于必须重试的通知、导入导出和关键业务操作。

        Args:
            coro: 待执行的协程对象
            name: 任务名称，用于日志关联和调试；为空时由 asyncio 自动命名

        Returns:
            已调度的 :class:`asyncio.Task` 实例，调用方可用于等待或取消
        """
        task = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        """任务完成回调：移除引用并记录异常。

        被取消的任务不记录异常（取消是正常行为）。
        异常任务记录到结构化日志（SPEC §20.1：任务异常必须记录日志）。
        """
        self._tasks.discard(task)

        if task.cancelled():
            return

        exc = task.exception()
        if exc is not None:
            _logger.error(
                "进程内任务异常",
                exc_info=exc,
                extra={"task_name": task.get_name()},
            )

    async def shutdown(self) -> None:
        """等待所有正在执行的任务完成。

        在应用关闭阶段调用，尽量让正在执行的任务完成。
        此方法不取消任务，只等待自然完成；调用方如需强制取消应自行处理。

        注意：即使调用此方法，进程被强制终止时仍未完成的任务可能丢失
        （SPEC §20.1）。
        """
        if not self._tasks:
            return
        await asyncio.gather(*self._tasks, return_exceptions=True)
