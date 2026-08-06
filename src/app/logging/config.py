"""日志配置入口（SPEC §24.1、§24.3）。

提供 :func:`configure_logging` 统一配置结构化 JSON 日志输出。
生产环境固定向标准输出输出一行一个 JSON 对象的结构化日志（SPEC §24.3）。
"""

from __future__ import annotations

import logging
import sys

from app.config.settings import Settings
from app.logging.formatter import JsonFormatter


def configure_logging(settings: Settings) -> None:
    """配置全局结构化 JSON 日志（SPEC §24.1、§24.3）。

    使用 JSON 格式化器替换 root logger 和 uvicorn logger 的 handler，
    确保所有日志统一输出到标准输出，一行一个 JSON 对象。

    Args:
        settings: 部署配置，用于确定运行环境和日志级别
    """
    formatter = JsonFormatter(environment=settings.app_env.value)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # 配置 root logger
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # 配置 uvicorn 日志使用同一格式化器，避免混合纯文本输出
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False
