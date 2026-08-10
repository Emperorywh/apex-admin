"""数据库基础设施包 — SPEC 8.1 / 8.2.

包含:
  - ``base``:       ORM 声明基类
  - ``engine``:     SQLAlchemy 2.0 异步引擎工厂
  - ``uow``:        SqlAlchemyUnitOfWork（SPEC 5.6）
  - ``exceptions``: SQLAlchemy 异常到应用异常的翻译
  - ``health``:     数据库健康检查器
  - ``migrations``: Alembic 迁移工具函数
"""
