"""Infrastructure 层 — 外部技术适配器（SPEC §5.2）。

Infrastructure 层实现 Application/Domain 内层定义的 Port，
将外部技术（SQLAlchemy、psycopg、文件系统）适配为稳定接口。

依赖方向：Infrastructure ──→ Application/Domain Port（SPEC §5.2）
Infrastructure 不得反向依赖 API 或 Application 的具体实现。
"""
