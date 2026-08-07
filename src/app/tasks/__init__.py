"""后台任务（SPEC §20）。

提供进程内轻量任务工具（SPEC §20.1）。

进程内轻量任务只用于可丢失、可快速完成、失败不影响核心业务的
数据处理。禁止用于必须重试的通知、导入导出和关键业务操作。
明确进程关闭时任务可能丢失。
"""

from __future__ import annotations

from app.tasks.background import InProcessTaskRunner

__all__ = ["InProcessTaskRunner"]
