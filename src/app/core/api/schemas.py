"""请求/响应 Schema 基类 — SPEC 9.2 / 9.3.

SPEC 9.2 约定:
  - 创建、全量更新和部分更新请求统一拒绝未知字段，Pydantic 模型
    使用 ``extra="forbid"``。
  - 请求参数使用明确的 Schema 校验。
  - 创建、更新、查询和响应模型按职责区分。
  - 更新接口明确区分全量更新和部分更新。

SPEC 9.3 序列化约定:
  - JSON 字段统一使用 ``camelCase``（请求、响应和查询参数一致）。
  - 时间字段统一为带时区的 ISO 8601 字符串。

``ApiModel`` 通过 ``alias_generator=to_camel`` 将 Python snake_case 字段名
自动转换为 camelCase JSON 键（如 ``display_name`` → ``displayName``），
对外契约（含 OpenAPI 文档）只呈现 camelCase。``populate_by_name=True``
允许服务端代码以 Python 字段名构造模型，同时兼容 snake_case 输入。

``StrictBaseModel`` 在 ``ApiModel`` 之上统一设置 ``extra="forbid"``，
所有创建、全量更新和部分更新请求模型应继承此基类。携带未知字段的请求
由 FastAPI 返回 422（通过 ``RequestValidationError`` → problem+json）。

时间字段由 Pydantic v2 默认序列化为带时区偏移的 ISO 8601 字符串。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """API Schema 统一基类 — camelCase 序列化（SPEC 9.3）.

    SPEC 9.3: "JSON 字段统一使用 camelCase（请求、响应和查询参数一致）"。

    - ``alias_generator=to_camel``: Python 字段保持 snake_case 命名，
      JSON 键自动转换为 camelCase；FastAPI 序列化响应模型时默认
      ``by_alias=True``，响应自动输出 camelCase。
    - ``populate_by_name=True``: 允许以 Python 字段名构造和校验模型，
      服务端内部代码无需感知别名。

    所有请求和响应 Schema 应直接或间接继承此基类。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


class StrictBaseModel(ApiModel):
    """请求 Schema 统一基类 — extra="forbid"（SPEC 9.2）.

    SPEC 9.2: "创建、全量更新和部分更新请求统一拒绝未知字段，
    Pydantic 模型使用 ``extra="forbid"``"。

    所有创建（Create）、全量更新（Update）和部分更新（Patch）请求
    模型应继承此基类。携带未知字段的请求由 Pydantic 拒绝，
    FastAPI 将其转换为 422 problem+json（SPEC 9.3）。

    职责区分约定（SPEC 9.2）:
      - **Create**: 创建请求，必填字段不含 ``id``。
      - **Update**: 全量更新请求，所有字段必填（PUT 语义）。
      - **Patch**: 部分更新请求，所有字段可选（PATCH 语义），
        但仍继承 ``extra="forbid"`` 拒绝未知字段。

    响应模型通常不使用 ``extra="forbid"``（响应可能有扩展字段），
    但字段命名遵循 camelCase 约定（继承自 ``ApiModel``）。
    """

    model_config = ConfigDict(extra="forbid")
