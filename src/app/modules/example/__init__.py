"""最小示例模块（SPEC §30.2、§34.1）。

此模块作为所有 G2–G4 模块任务的模板，演示完整的模块接入模式：
Router、Use Case、Application Port、Domain Policy、Repository Adapter、
Alembic 迁移、权限点、错误码、领域事件和测试。

示例模块不携带业务演示数据（SPEC §30.2）。
"""

from __future__ import annotations
