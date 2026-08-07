"""示例模块基础设施层（SPEC §5.2）。

包含 SQLAlchemy ORM 模型、Repository Adapter、模块工作单元实现、
事件处理器实现和服务装配工厂。基础设施层实现应用层定义的端口，
不反向依赖 API 层。
"""

from __future__ import annotations
