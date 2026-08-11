"""审计查询请求与响应 Schema — SPEC 9.2 / 9.3 / 9.4 / 18.3.

SPEC 9.2: 创建、全量更新请求拒绝未知字段（``extra="forbid"``）。
SPEC 9.3: JSON 字段统一 snake_case。
SPEC 9.4: 分页响应固定为 ``{items, total, page, page_size, pages}``。

SPEC 18.3:
  - 分页查询登录日志与操作审计。
  - 按操作者、模块、动作、资源、结果和时间范围筛选。
  - 查看单次操作详情。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel

from app.core.api.pagination import PageResponse


class AuditLogResponse(BaseModel):
    """审计日志响应模型 — SPEC 9.3 / 18.3."""

    model_config = {"extra": "forbid"}

    id: UUID
    actor_id: str | None
    actor_display_name: str
    module: str
    action: str
    resource_type: str
    resource_id: str | None
    resource_display_name: str | None
    result: str
    request_id: str | None
    diff: dict[str, dict[str, Any]] | None
    occurred_at: datetime


class LoginLogResponse(BaseModel):
    """登录日志响应模型 — SPEC 9.3 / 18.1 / 18.3."""

    model_config = {"extra": "forbid"}

    id: UUID
    user_id: str | None
    username: str
    session_id: str | None
    ip_address: str
    user_agent: str | None
    result: str
    failure_reason: str | None
    occurred_at: datetime


class AuditLogPageResponse(PageResponse[AuditLogResponse]):
    """审计日志分页响应 — SPEC 9.4 / 18.3."""


class LoginLogPageResponse(PageResponse[LoginLogResponse]):
    """登录日志分页响应 — SPEC 9.4 / 18.3."""
