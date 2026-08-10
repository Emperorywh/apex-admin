"""审计与登录日志 ORM 模型 — SPEC 8.3 / 18.1 / 18.2.

SPEC 8.3 数据建模规范:
  - 每张业务表具有明确主键。
  - 表名、字段名遵循统一规范。
  - 时间字段使用 ``timestamptz``，统一 UTC（SPEC 6.3）。
  - 审计日志等不可变数据不得通过通用 CRUD 随意修改。

审计日志和登录日志表设计为仅追加（append-only）:
  - 无 ``updated_at`` 列（不可变，不支持修改）。
  - 无 UPDATE / DELETE 应用层路径（Adapter 仅提供 INSERT）。
  - ORM 模型只在 Infrastructure 层使用，不泄漏到 Application 或 API 层
    （SPEC 5.2: "Infrastructure 只实现内层 Port，不得在内层暴露
    SQLAlchemy 类型"）。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class AuditLogORM(Base):
    """操作审计 ORM 模型 — 映射 ``audit_logs`` 表（SPEC 18.2）.

    SPEC 8.3 / 18.2: 审计日志不可变，不提供通用 CRUD 修改路径。
    此表仅通过 ``SqlAlchemyAuditRepository.record_audit`` 执行 INSERT，
    不存在 UPDATE 或 DELETE 的应用层路径。

    SPEC 18.2 字段:
      - ``actor_id`` / ``actor_display_name``: 操作者身份和显示名快照。
      - ``module`` / ``action``: 操作模块和动作。
      - ``resource_type`` / ``resource_id`` / ``resource_display_name``:
        目标资源类型、标识和显示名快照。
      - ``result`` / ``request_id``: 操作结果和请求标识。
      - ``diff``: 变更差异（JSONB，字段白名单生成）。
      - ``occurred_at``: 操作发生时间。

    显示名快照字段（``actor_display_name``、``resource_display_name``）
    在操作发生时写入，后续源数据变更不影响历史审计记录
    （SPEC 18.2: "操作者显示名称、目标显示名称等易变信息按操作发生时
    快照保存"）。
    """

    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resource_display_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    result: Mapped[str] = mapped_column(String(50), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class LoginLogORM(Base):
    """登录日志 ORM 模型 — 映射 ``login_logs`` 表（SPEC 18.1）.

    SPEC 8.3: 登录日志不可变，不提供通用 CRUD 修改路径。
    此表仅通过 ``SqlAlchemyLoginLogRepository.record_login`` 执行 INSERT，
    不存在 UPDATE 或 DELETE 的应用层路径。

    SPEC 18.1 字段:
      - ``user_id`` / ``username``: 用户标识和登录账号。
      - ``session_id``: 会话标识。
      - ``ip_address`` / ``user_agent``: 客户端信息。
      - ``result`` / ``failure_reason``: 登录结果和失败原因分类。
      - ``occurred_at``: 发生时间。

    SPEC 18.1 / 12.4: "不记录明文密码和完整 Token"。
    此表不包含密码和 Token 列。
    """

    __tablename__ = "login_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    username: Mapped[str] = mapped_column(String(200), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(100), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(String(50), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
