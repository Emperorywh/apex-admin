"""请求/响应 Schema 基础模式（SPEC §9.2、§9.3）。

定义所有模块共享的 Schema 基类和命名约定，
确保 API 层的请求校验和响应序列化行为一致。

Schema 约定（SPEC §9.2、§9.3）：
    - 创建、全量更新和部分更新请求 Schema 统一拒绝未知字段（``extra="forbid"``）
    - JSON 字段统一使用 ``snake_case``
    - 时间字段统一使用带时区的 ISO 8601 字符串
    - 普通成功响应直接返回资源 Schema，不使用 ``{code, message, data}`` 信封
    - 创建成功返回 HTTP 201，并在适用时返回 ``Location``
    - 无响应体的删除成功返回 HTTP 204
    - 文件和流式响应不套 JSON 信封

snake_case 约定：
    Pydantic 模型的字段名即 JSON 键名。定义字段时使用 ``snake_case``，
    序列化结果自动为 ``snake_case``，无需额外配置。

ISO 8601 时间约定（SPEC §6.3、§9.3）：
    使用 Python ``datetime`` 类型并确保时区感知（``tzinfo`` 非空）。
    Pydantic v2 将时区感知的 ``datetime`` 序列化为带时区的 ISO 8601 字符串，
    例如 ``"2026-07-24T12:00:00+00:00"``。禁止使用无时区的 ``datetime`` 参与
    关键业务计算和 API 响应。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseRequestModel(BaseModel):
    """创建/更新请求 Schema 基类（SPEC §9.2）。

    所有创建、全量更新和部分更新请求 Schema 继承此类。
    ``extra="forbid"`` 确保请求体中的未知字段被统一拒绝，
    防止客户端传入未预期的参数（SPEC §9.2）。

    子类只需声明业务字段，无需重复设置 ``model_config``::

        class UserCreate(BaseRequestModel):
            username: str
            display_name: str

    字段命名使用 ``snake_case``（SPEC §9.3）。
    """

    model_config = ConfigDict(extra="forbid")


class BaseResponseModel(BaseModel):
    """响应 Schema 基类（SPEC §9.3）。

    所有资源响应 Schema 继承此类。响应直接返回资源 Schema，
    不使用 ``{code, message, data}`` 成功信封（SPEC §9.3）。

    ``extra="forbid"`` 防止意外泄露未声明的字段（如 ORM 模型的内部字段或
    敏感字段），确保响应结构严格匹配 Schema 定义。

    字段命名使用 ``snake_case``；时间字段使用带时区的 ISO 8601 字符串
    （SPEC §9.3、§6.3）。
    """

    model_config = ConfigDict(extra="forbid")
