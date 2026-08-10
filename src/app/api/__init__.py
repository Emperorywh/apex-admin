"""API 层.

SPEC 5.2: 包含 Router、Request/Response Schema 和 API 版本管理。
API 层依赖 Application 层和 Domain 层，禁止直接导入 Infrastructure 层
（禁止路由层直接访问数据库）。
"""
