"""请求/响应 Schema 基类 — SPEC 9.2 / 9.3.

SPEC 9.2 约定:
  - 创建、全量更新和部分更新请求统一拒绝未知字段，Pydantic 模型
    使用 ``extra="forbid"``。
  - 请求参数使用明确的 Schema 校验。
  - 创建、更新、查询和响应模型按职责区分。
  - 更新接口明确区分全量更新和部分更新。

SPEC 9.3 序列化约定:
  - JSON 字段统一使用 ``snake_case``。
  - 时间字段统一为带时区的 ISO 8601 字符串。

``StrictBaseModel`` 统一设置 ``extra="forbid"``，所有创建、全量更新
和部分更新请求模型应继承此基类。携带未知字段的请求由 FastAPI
返回 422（通过 ``RequestValidationError`` → problem+json）。

Pydantic v2 默认行为已满足 snake_case 和带时区 ISO 8601 的序列化约定：
  - Python 字段命名约定为 snake_case，Pydantic 以字段名作为 JSON 键，
    无需额外的 alias_generator。
  - timezone-aware datetime 序列化为包含时区偏移的 ISO 8601 字符串。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictBaseModel(BaseModel):
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
    但字段命名遵循 snake_case 约定。
    """

    model_config = ConfigDict(extra="forbid")
