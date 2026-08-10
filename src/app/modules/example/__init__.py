"""最小示例模块 — SPEC 30.2 / 34.1.

此模块是一个完整样板，端到端演示 Router → Use Case → Port → Adapter →
迁移 → 权限点 → 错误码 → 审计动作 → 初始化器 → 事务内事件 → 测试的
完整接入过程（SPEC 5.5）。

派生项目可整体删除此模块：删除 ``src/app/modules/example/`` 目录，
移除 ``composition/modules.py`` 中 ``MODULE_MANIFEST`` 的对应条目，
删除迁移版本目录和 ``alembic upgrade`` 记录即可。
"""
