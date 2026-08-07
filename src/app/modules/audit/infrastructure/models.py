"""审计模块 SQLAlchemy ORM 模型（SPEC §5.4、§18.1–18.2）。

ORM 模型与领域实体分离——Repository Adapter 负责在两者之间转换
（SPEC §5.2：禁止把 ORM 模型直接作为 API 响应模型）。

表结构通过 Alembic 迁移文件创建（手写 DDL，SPEC §8.2）。

安全约束（SPEC §18.2、§23.2）：
- 审计记录为不可变追加日志——ORM 模型不暴露 update/delete 路由
- 变更差异以 JSON 存储白名单生成的已过滤差异
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.modules.audit.domain.model import (
    AuditLog,
    LoginLog,
    LoginResult,
)
from app.ports.audit import AuditDiff, AuditResult, FieldChange


class Base(DeclarativeBase):
    """审计模块 ORM 声明基类。

    各模块维护自身的 ``DeclarativeBase``；迁移文件手写 DDL，
    此基类仅用于运行时 ORM 操作。
    """


class AuditDiffType(TypeDecorator[AuditDiff]):
    """审计差异 JSON 自定义类型。

    将 :class:`~app.modules.audit.domain.diff.AuditDiff` 在领域对象和
    数据库 JSON 文本之间转换。存储时序列化为 JSON 数组（字段名 + 前后值），
    读取时还原为 :class:`AuditDiff`。

    使用 JSON 文本列存储（而非 JSONB），因为 PostgreSQL 的 JSONB 需要
    ``json`` 类型导入，且审计差异为只读追加数据，查询性能不是首要目标。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(
        self,
        value: AuditDiff | None,
        dialect: object,  # noqa: ARG002
    ) -> str | None:
        """将 AuditDiff 序列化为 JSON 文本。"""
        if value is None:
            return None
        return json.dumps(
            [{"field": c.field, "old": c.old, "new": c.new} for c in value.changes],
            ensure_ascii=False,
        )

    def process_result_value(
        self,
        value: str | None,
        dialect: object,  # noqa: ARG002
    ) -> AuditDiff | None:
        """从 JSON 文本还原 AuditDiff。"""
        if value is None:
            return None
        items = json.loads(value)
        changes = tuple(
            FieldChange(field=item["field"], old=item["old"], new=item["new"]) for item in items
        )
        return AuditDiff(changes=changes)


class AuditLogModel(Base):
    """操作审计 ORM 模型（SPEC §18.2）。

    表名 ``audit_logs``，通过 Alembic 迁移 ``0007_audit`` 创建。

    审计记录为不可变追加日志——不提供 update 或 delete 操作
    （SPEC §18.2：审计日志不通过普通业务 CRUD 修改）。

    Attributes:
        id: 审计记录 UUID 主键
        actor_id: 操作者 ID（可空——未认证操作）
        actor_display_name: 操作者显示名称快照
        occurred_at: 操作时间（UTC，带时区）
        module: 操作模块编码
        action: 操作动作编码
        resource_type: 目标资源类型编码（可空）
        resource_id: 目标资源标识（可空）
        resource_display_name: 目标显示名称快照（可空）
        result: 操作结果（``success`` / ``failed``）
        request_id: 请求 ID（可空）
        diff: 变更差异 JSON（可空）
    """

    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    actor_display_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_display_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    diff: Mapped[AuditDiff | None] = mapped_column(
        AuditDiffType,
        nullable=True,
    )

    @staticmethod
    def from_entity(entity: AuditLog) -> AuditLogModel:
        """从领域实体构造 ORM 模型。"""
        return AuditLogModel(
            id=entity.id,
            actor_id=entity.actor_id,
            actor_display_name=entity.actor_display_name,
            occurred_at=entity.occurred_at,
            module=entity.module,
            action=entity.action,
            resource_type=entity.resource_type,
            resource_id=entity.resource_id,
            resource_display_name=entity.resource_display_name,
            result=entity.result.value,
            request_id=entity.request_id,
            diff=entity.diff,
        )

    def to_entity(self) -> AuditLog:
        """转换为领域实体。"""
        occurred_at = self.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return AuditLog(
            id=self.id,
            actor_id=self.actor_id,
            actor_display_name=self.actor_display_name,
            occurred_at=occurred_at,
            module=self.module,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            resource_display_name=self.resource_display_name,
            result=AuditResult(self.result),
            request_id=self.request_id,
            diff=self.diff,
        )


class LoginLogModel(Base):
    """登录日志 ORM 模型（SPEC §18.1）。

    表名 ``login_logs``，通过 Alembic 迁移 ``0007_audit`` 创建。

    登录日志为不可变追加日志——不提供 update 或 delete 操作
    （SPEC §18.2：审计日志不通过普通业务 CRUD 修改）。

    Attributes:
        id: 登录日志 UUID 主键
        user_id: 用户 ID（可空——用户不存在时）
        username: 用户名（可空）
        session_id: 会话 ID（可空）
        ip: 客户端 IP
        user_agent: 客户端 User-Agent
        occurred_at: 发生时间（UTC，带时区）
        result: 登录结果
        failure_reason: 失败原因（可空）
    """

    __tablename__ = "login_logs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(500), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    @staticmethod
    def from_entity(entity: LoginLog) -> LoginLogModel:
        """从领域实体构造 ORM 模型。"""
        return LoginLogModel(
            id=entity.id,
            user_id=entity.user_id,
            username=entity.username,
            session_id=entity.session_id,
            ip=entity.ip,
            user_agent=entity.user_agent,
            occurred_at=entity.occurred_at,
            result=entity.result.value,
            failure_reason=entity.failure_reason,
        )

    def to_entity(self) -> LoginLog:
        """转换为领域实体。"""
        occurred_at = self.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return LoginLog(
            id=self.id,
            user_id=self.user_id,
            username=self.username,
            session_id=self.session_id,
            ip=self.ip,
            user_agent=self.user_agent,
            occurred_at=occurred_at,
            result=LoginResult(self.result),
            failure_reason=self.failure_reason,
        )
