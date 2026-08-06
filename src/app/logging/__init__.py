"""结构化 JSON 日志（SPEC §24.1）。

生产环境固定向标准输出输出一行一个 JSON 对象的结构化日志（SPEC §24.3）。
日志包含时间戳、级别、环境、模块和 Request ID，并过滤密码、Token、Cookie、
密钥等敏感字段。

本包提供 JSON 格式化器和日志配置入口。Request ID 关联通过
:mod:`app.middleware.request_id` 的 ContextVar 实现。
"""

from app.logging.config import configure_logging
from app.logging.formatter import JsonFormatter

__all__ = ["JsonFormatter", "configure_logging"]
