"""RFC 9457 Problem Details 响应模型（SPEC §9.3）。

所有 API 错误响应使用此模型，Content-Type 固定为 ``application/problem+json``。
响应固定包含 type、title、status、detail、instance、code、request_id 字段；
字段校验错误额外包含 errors 数组（SPEC §9.3）。

本模型不使用 ``{code, message, data}`` 成功信封，不将业务错误包装为 HTTP 200。
``detail`` 和 ``message`` 只供展示，客户端业务判断只能使用稳定 ``code``。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.errors.base import FieldError


class ProblemDetail(BaseModel):
    """RFC 9457 Problem Details JSON 响应模型（SPEC §9.3）。

    所有 API 错误响应统一使用此模型。

    固定字段（SPEC §9.3）:
        type: URI；业务错误为 ``urn:apex:problem:<小写错误码>``，
              框架级错误为 ``about:blank``
        title: 问题类型的简短摘要
        status: HTTP 状态码
        detail: 具体问题的描述，仅供展示，客户端业务判断只能使用 code
        instance: 请求路径 URI
        code: 稳定错误码，格式 ``<MODULE>.<REASON>``（SPEC §10.2）
        request_id: 请求唯一标识，用于日志关联和审计追踪

    可选字段:
        errors: 字段校验错误数组，元素含 field、reason、message（SPEC §9.3）
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str
    errors: list[FieldError] | None = None
