"""Infrastructure 层.

SPEC 5.2: 包含 Infrastructure Adapter（SqlAlchemyUnitOfWork、
Repository Adapter 等），只实现 Application 或 Domain 定义的 Port。
Infrastructure 层不得在内层暴露 SQLAlchemy、FastAPI 或具体 SDK 类型，
禁止反向依赖 API 层或 Composition Root。
"""
