"""审计查询请求与响应 Schema — SPEC 9.2 / 9.3 / 9.4 / 18.3.

SPEC 9.2: 创建、全量更新请求拒绝未知字段（``extra="forbid"``）。
SPEC 9.3: JSON 字段统一 camelCase。
SPEC 9.4: 分页响应固定为 ``{items, total, page, pageSize, pages}``。

SPEC 18.3:
  - 分页查询登录日志与操作审计。
  - 按操作者、模块、动作、资源、结果和时间范围筛选。
  - 查看单次操作详情。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import field_serializer
from pydantic.alias_generators import to_camel

from app.core.api.pagination import PageResponse
from app.core.api.schemas import ApiModel


class AuditLogResponse(ApiModel):
    """审计日志响应模型 — SPEC 9.3 / 18.3.

    ``diff`` 的键为领域字段名（内部 snake_case），序列化时转换为
    camelCase，与整体 JSON 命名约定一致（SPEC 9.3）。
    """

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

    @field_serializer("diff")
    def _serialize_diff_keys(
        self,
        diff: dict[str, dict[str, Any]] | None,
    ) -> dict[str, dict[str, Any]] | None:
        """将 diff 的字段名键序列化为 camelCase（SPEC 9.3）。"""

        if diff is None:
            return None
        return {to_camel(key): value for key, value in diff.items()}


class LoginLogResponse(ApiModel):
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
