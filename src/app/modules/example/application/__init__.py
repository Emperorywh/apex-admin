"""示例模块应用层（SPEC §5.2）。

包含 Application Port（模块公开接口和数据访问端口）、请求/响应 Schema
和 Use Case（应用服务）。应用层依赖领域层和 Application Port，
不依赖基础设施具体实现。
"""

from __future__ import annotations
