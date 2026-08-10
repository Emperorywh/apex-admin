"""进程内轻量任务工具 — SPEC 20.1.

适用范围:
  - 只用于可丢失、可快速完成、失败不影响核心业务的数据处理。
  - 禁止用于必须重试的通知、导入导出和关键业务操作。

行为约束:
  - 进程关闭时正在执行的任务可能丢失，不保证完成。
  - 任务异常必须记录结构化日志，不吞掉异常。
  - 任务基于 ``asyncio.create_task``，生命周期绑定当前事件循环。

此工具不实现持久化、重试或跨进程协调。需要可靠送达的场景
必须使用持久化后台任务扩展（SPEC 20.2，EXT）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Coroutine


class InProcessTaskRunner:
    """进程内轻量任务运行器.

    封装 ``asyncio.create_task``，统一管理后台任务的异常记录。
    每个运行器实例维护一组活跃任务引用，防止被垃圾回收。

    重要限制（SPEC 20.1）:
      - 进程关闭时未完成的任务会丢失。
      - 不提供重试、持久化或跨进程能力。
      - 禁止用于关键业务操作。
    """

    def __init__(self) -> None:
        """初始化任务运行器，创建活跃任务集合。"""

        self._tasks: set[Any] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """提交一个协程作为后台轻量任务.

        任务被创建后立即加入事件循环调度。异常会在完成回调中
        记录结构化日志，不会被静默吞掉。

        参数:
            coro: 待执行的协程对象。

        返回:
            创建的 ``asyncio.Task`` 实例，可用于 await 或取消。
        """

        import asyncio

        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: Any) -> None:
        """任务完成回调：从活跃集合移除，异常时记录日志.

        SPEC 20.1: 任务异常必须记录日志，不吞掉异常。
        被取消的任务不视为异常。

        日志记录器在方法内部获取（而非模块级缓存），确保
        拾取最新的 structlog 配置（如测试中的 capture_logs）。
        """

        self._tasks.discard(task)
        if task.cancelled():
            return

        exc = task.exception()
        if exc is not None:
            # 每次获取新 logger 实例，拾取当前 structlog 配置
            logger = structlog.get_logger().bind(module="app.core.tasks")
            logger.error(
                "轻量任务异常",
                task_name=task.get_name(),
                error_type=type(exc).__name__,
                error_message=str(exc),
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    @property
    def pending_count(self) -> int:
        """当前活跃（未完成）的任务数量。"""

        return len(self._tasks)
