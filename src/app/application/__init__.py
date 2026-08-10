"""Application 层.

SPEC 5.2: 包含 Application Service、Use Case 和 Port（Repository、
UnitOfWork、Clock、ID Generator 等）。
Port 由 Application 或 Domain 内层定义，Infrastructure 只实现这些 Port。
Application 层依赖 Domain 层，禁止反向依赖 API、Infrastructure 或 Composition。
"""
