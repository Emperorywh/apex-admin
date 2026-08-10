"""进程内轻量任务工具测试 — SPEC 20.1.

覆盖:
  - 任务异常被记录不吞掉。
  - 正常任务完成后从活跃集合移除。
  - pending_count 反映活跃任务数。
  - 被取消的任务不记录异常。
"""

from __future__ import annotations

import asyncio

import pytest
import structlog

from app.core.tasks import InProcessTaskRunner


@pytest.mark.g1
@pytest.mark.unit
async def test_task_success_completes() -> None:
    """正常任务完成后从活跃集合移除。"""

    runner = InProcessTaskRunner()
    assert runner.pending_count == 0

    async def quick_task() -> int:
        await asyncio.sleep(0.01)
        return 42

    task = runner.spawn(quick_task())
    assert runner.pending_count == 1

    result = await task
    assert result == 42

    # 等待回调执行（add_done_callback 在下一事件循环迭代触发）
    await asyncio.sleep(0.01)
    assert runner.pending_count == 0


@pytest.mark.g1
@pytest.mark.unit
async def test_task_exception_logged_not_swallowed() -> None:
    """任务异常被记录结构化日志，不静默吞掉（SPEC 20.1）.

    使用 structlog.testing.capture_logs 捕获日志输出，
    验证异常类型和消息被记录到 error 级别日志。
    """

    runner = InProcessTaskRunner()

    async def failing_task() -> None:
        msg = "轻量任务故意失败"
        raise RuntimeError(msg)

    with structlog.testing.capture_logs() as cap_logs:
        task = runner.spawn(failing_task())
        # 等待任务完成和回调执行
        await asyncio.sleep(0.05)

    # 异常被记录到 error 级别
    error_logs = [log for log in cap_logs if log["log_level"] == "error"]
    assert len(error_logs) == 1
    log_entry = error_logs[0]
    assert log_entry["event"] == "轻量任务异常"
    assert "RuntimeError" in log_entry["error_type"]
    assert "轻量任务故意失败" in log_entry["error_message"]

    # 任务对象本身仍然标记为完成（异常被捕获到日志）
    assert task.done()
    assert task.exception() is not None


@pytest.mark.g1
@pytest.mark.unit
async def test_cancelled_task_not_logged_as_error() -> None:
    """被取消的任务不记录异常日志。"""

    runner = InProcessTaskRunner()

    async def long_task() -> None:
        await asyncio.sleep(10)

    with structlog.testing.capture_logs() as cap_logs:
        task = runner.spawn(long_task())
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)

    # 不应有 error 级别日志
    error_logs = [log for log in cap_logs if log["log_level"] == "error"]
    assert len(error_logs) == 0
    assert task.cancelled()


@pytest.mark.g1
@pytest.mark.unit
async def test_pending_count_tracks_multiple_tasks() -> None:
    """pending_count 准确跟踪多个活跃任务。"""

    runner = InProcessTaskRunner()

    async def blocking_task() -> None:
        await asyncio.sleep(0.05)

    runner.spawn(blocking_task())
    runner.spawn(blocking_task())
    runner.spawn(blocking_task())
    assert runner.pending_count == 3

    await asyncio.sleep(0.1)
    assert runner.pending_count == 0
