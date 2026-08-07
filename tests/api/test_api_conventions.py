"""API 通用规范契约测试（SPEC §9.1–9.4、§20.1、§28.4）。

覆盖验收条件：
- 分页参数约束和 {items, total, page, page_size, pages} 响应
- 排序参数逗号分隔、降序前缀和白名单拒绝
- 创建/更新请求 Schema extra=forbid
- JSON 字段 snake_case、时间字段带时区 ISO 8601
- 成功直接返回资源（无信封）；创建 201 含 Location；删除 204
- 文件/流式响应不使用 JSON 信封
- 进程内任务工具文档和行为
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from starlette.testclient import TestClient

from app.api.handlers import register_exception_handlers
from app.api.pagination import (
    Page,
    PaginationParams,
    get_pagination_params,
    paginate,
)
from app.api.schemas import BaseRequestModel, BaseResponseModel
from app.api.sorting import SortInstruction, parse_sort
from app.errors.base import ParameterError
from app.middleware.request_id import RequestIdMiddleware
from app.tasks.background import InProcessTaskRunner

pytestmark = [pytest.mark.api, pytest.mark.g1]


# ---------------------------------------------------------------------------
# 测试用 Schema（SPEC §9.2、§9.3）
# ---------------------------------------------------------------------------


class ItemCreate(BaseRequestModel):
    """创建资源请求 Schema（SPEC §9.2：extra=forbid）。"""

    name: str
    quantity: int


class ItemUpdate(BaseRequestModel):
    """全量更新请求 Schema（SPEC §9.2：extra=forbid）。"""

    name: str
    quantity: int


class ItemPatch(BaseRequestModel):
    """部分更新请求 Schema（SPEC §9.2：extra=forbid）。"""

    name: str | None = None
    quantity: int | None = None


class ItemResponse(BaseResponseModel):
    """资源响应 Schema（SPEC §9.3：直接返回资源，无信封）。

    使用 snake_case 字段名和带时区的 datetime（SPEC §9.3、§6.3）。
    """

    id: int
    name: str
    quantity: int
    created_at: datetime


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

_now = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)

_store: dict[int, dict[str, Any]] = {
    1: {"id": 1, "name": "alpha", "quantity": 10, "created_at": _now},
    2: {"id": 2, "name": "beta", "quantity": 20, "created_at": _now},
    3: {"id": 3, "name": "gamma", "quantity": 30, "created_at": _now},
}

# 排序白名单（SPEC §9.4：每查询显式声明）
_ALLOWED_SORT_FIELDS = frozenset({"name", "created_at", "quantity"})


# ---------------------------------------------------------------------------
# 测试应用工厂
# ---------------------------------------------------------------------------


def _create_test_app() -> FastAPI:
    """创建包含 API 通用规范演示路由的 FastAPI 应用。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    # ---- GET /items：分页 + 排序，直接返回 Page（无信封）----

    @app.get("/items", response_model=Page[ItemResponse], summary="分页查询资源")
    async def list_items(
        pagination: PaginationParams = Depends(get_pagination_params),  # noqa: B008
        sort: str | None = None,
    ) -> Page[ItemResponse]:
        # SPEC §9.4：排序字段使用每查询显式声明的白名单
        sort_instructions = parse_sort(sort, _ALLOWED_SORT_FIELDS)

        items = list(_store.values())

        # 按 sort 指令排序
        for instruction in reversed(sort_instructions):
            items.sort(
                key=lambda x: x[instruction.field],
                reverse=instruction.descending,
            )

        # 分页切片
        total = len(items)
        start = (pagination.page - 1) * pagination.page_size
        end = start + pagination.page_size
        page_items = items[start:end]

        return paginate(
            items=[ItemResponse(**item) for item in page_items],
            total=total,
            pagination=pagination,
        )

    # ---- POST /items：创建 → 201 + Location + 直接返回资源 ----

    @app.post(
        "/items",
        response_model=ItemResponse,
        status_code=status.HTTP_201_CREATED,
        summary="创建资源",
    )
    async def create_item(
        body: ItemCreate,
        response: Response,
    ) -> ItemResponse:
        new_id = max(_store.keys()) + 1
        item_data = {
            "id": new_id,
            "name": body.name,
            "quantity": body.quantity,
            "created_at": _now,
        }
        _store[new_id] = item_data
        response.headers["Location"] = f"/items/{new_id}"
        return ItemResponse(**item_data)

    # ---- DELETE /items/{id}：无体删除 → 204 ----

    @app.delete(
        "/items/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="删除资源",
    )
    async def delete_item(item_id: int) -> None:
        if item_id not in _store:
            raise ParameterError("资源不存在", code="APP.NOT_FOUND")
        del _store[item_id]
        # 无返回体 → 204

    # ---- GET /items/{id}/download：文件/流式响应（不套 JSON 信封）----

    @app.get("/items/{item_id}/download", summary="下载资源文件")
    async def download_item(item_id: int) -> PlainTextResponse:
        if item_id not in _store:
            raise ParameterError("资源不存在", code="APP.NOT_FOUND")
        # 文件/流式响应直接返回原始内容，不使用 JSON 信封（SPEC §9.3）
        return PlainTextResponse(
            content=f"file-content-for-item-{item_id}",
            media_type="text/plain",
        )

    return app


@pytest.fixture
def client() -> TestClient:
    """创建测试客户端。"""
    return TestClient(_create_test_app(), raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_store() -> Any:
    """每个测试前重置数据存储，确保测试间无状态依赖。"""
    _store.clear()
    _store.update(
        {
            1: {"id": 1, "name": "alpha", "quantity": 10, "created_at": _now},
            2: {"id": 2, "name": "beta", "quantity": 20, "created_at": _now},
            3: {"id": 3, "name": "gamma", "quantity": 30, "created_at": _now},
        }
    )
    yield
    _store.clear()
    _store.update(
        {
            1: {"id": 1, "name": "alpha", "quantity": 10, "created_at": _now},
            2: {"id": 2, "name": "beta", "quantity": 20, "created_at": _now},
            3: {"id": 3, "name": "gamma", "quantity": 30, "created_at": _now},
        }
    )


# ===========================================================================
# 验收条件 0：分页参数与响应结构（SPEC §9.4）
# ===========================================================================


class TestPaginationConvention:
    """验证页码分页参数约束和响应结构。"""

    def test_default_pagination(self, client: TestClient) -> None:
        """默认 page=1, page_size=20，响应含 {items, total, page, page_size, pages}。"""
        response = client.get("/items")
        assert response.status_code == 200

        body: dict[str, Any] = response.json()
        # 响应固定字段（SPEC §9.4）
        assert set(body.keys()) == {"items", "total", "page", "page_size", "pages"}
        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["total"] == 3
        assert body["pages"] == 1

    def test_custom_page_size(self, client: TestClient) -> None:
        """page_size=2 正确分页。"""
        response = client.get("/items?page_size=2")
        body = response.json()
        assert body["page_size"] == 2
        assert body["total"] == 3
        assert body["pages"] == 2
        assert len(body["items"]) == 2

    def test_page_2(self, client: TestClient) -> None:
        """page=2, page_size=2 返回第二页。"""
        response = client.get("/items?page=2&page_size=2")
        body = response.json()
        assert body["page"] == 2
        assert len(body["items"]) == 1

    def test_page_below_minimum_rejected(self, client: TestClient) -> None:
        """page=0 被 Query 约束拒绝（422）。"""
        response = client.get("/items?page=0")
        assert response.status_code == 422

    def test_page_size_exceeds_max_rejected(self, client: TestClient) -> None:
        """page_size=101 被约束拒绝（422）。"""
        response = client.get("/items?page_size=101")
        assert response.status_code == 422

    def test_page_size_zero_rejected(self, client: TestClient) -> None:
        """page_size=0 被约束拒绝（422）。"""
        response = client.get("/items?page_size=0")
        assert response.status_code == 422

    def test_negative_page_rejected(self, client: TestClient) -> None:
        """page=-1 被约束拒绝（422）。"""
        response = client.get("/items?page=-1")
        assert response.status_code == 422


# ===========================================================================
# 验收条件 1：排序参数（SPEC §9.4）
# ===========================================================================


class TestSortingConvention:
    """验证排序参数解析和白名单校验。"""

    def test_ascending_sort(self, client: TestClient) -> None:
        """sort=name 按名称升序排列。"""
        response = client.get("/items?sort=name")
        body = response.json()
        names = [item["name"] for item in body["items"]]
        assert names == ["alpha", "beta", "gamma"]

    def test_descending_sort(self, client: TestClient) -> None:
        """sort=-name 按名称降序排列。"""
        response = client.get("/items?sort=-name")
        body = response.json()
        names = [item["name"] for item in body["items"]]
        assert names == ["gamma", "beta", "alpha"]

    def test_multiple_sort_fields(self, client: TestClient) -> None:
        """sort=-quantity,name 多字段排序。"""
        response = client.get("/items?sort=-quantity,name")
        body = response.json()
        quantities = [item["quantity"] for item in body["items"]]
        assert quantities == [30, 20, 10]

    def test_sort_field_not_in_whitelist_rejected(self, client: TestClient) -> None:
        """排序字段不在白名单中返回参数错误（400）。"""
        response = client.get("/items?sort=password")
        assert response.status_code == 400
        assert response.headers["content-type"] == "application/problem+json"

        body = response.json()
        assert body["code"] == "APP.PARAMETER"

    def test_no_sort_returns_default_order(self, client: TestClient) -> None:
        """无 sort 参数时不报错。"""
        response = client.get("/items")
        assert response.status_code == 200


# ===========================================================================
# 验收条件 2：extra=forbid（SPEC §9.2）
# ===========================================================================


class TestExtraForbid:
    """验证创建/更新请求 Schema 拒绝未知字段。"""

    def test_create_rejects_unknown_field(self, client: TestClient) -> None:
        """POST 含未知字段时返回 422。"""
        response = client.post(
            "/items",
            json={"name": "delta", "quantity": 5, "evil_field": "bad"},
        )
        assert response.status_code == 422

    def test_create_accepts_valid_body(self, client: TestClient) -> None:
        """POST 合法请求体创建成功。"""
        response = client.post("/items", json={"name": "delta", "quantity": 5})
        assert response.status_code == 201

    def test_request_model_extra_forbid_directly(self) -> None:
        """BaseRequestModel 子类直接拒绝未知字段。"""
        with pytest.raises(ValidationError) as exc_info:
            ItemCreate(name="x", quantity=1, extra="bad")  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        assert any(e["type"] == "extra_forbidden" for e in errors)

    def test_update_model_extra_forbid(self) -> None:
        """更新请求 Schema 也拒绝未知字段。"""
        with pytest.raises(ValidationError):
            ItemUpdate(name="x", quantity=1, extra="bad")  # type: ignore[call-arg]

    def test_patch_model_extra_forbid(self) -> None:
        """部分更新请求 Schema 也拒绝未知字段。"""
        with pytest.raises(ValidationError):
            ItemPatch(name="x", extra="bad")  # type: ignore[call-arg]


# ===========================================================================
# 验收条件 3：snake_case 和 ISO 8601（SPEC §9.3、§6.3）
# ===========================================================================


class TestJsonNamingAndTimeFormat:
    """验证 JSON 字段 snake_case 和时间字段带时区 ISO 8601。"""

    def test_response_uses_snake_case(self, client: TestClient) -> None:
        """响应字段使用 snake_case，不使用 camelCase。"""
        response = client.get("/items")
        body = response.json()
        first_item = body["items"][0]
        # snake_case 字段存在
        assert "created_at" in first_item
        # camelCase 不存在
        assert "createdAt" not in first_item

    def test_time_field_is_iso_8601_with_timezone(self, client: TestClient) -> None:
        """时间字段为带时区的 ISO 8601 字符串。"""
        response = client.get("/items")
        body = response.json()
        created_at = body["items"][0]["created_at"]

        # 应为 ISO 8601 字符串，包含时区偏移（+00:00）
        assert isinstance(created_at, str)
        assert "+00:00" in created_at or "Z" in created_at

        # 可解析回 datetime
        parsed = datetime.fromisoformat(created_at)
        assert parsed.tzinfo is not None

    def test_pagination_fields_snake_case(self, client: TestClient) -> None:
        """分页响应字段使用 snake_case。"""
        response = client.get("/items")
        body = response.json()
        assert "page_size" in body
        assert "pageSize" not in body


# ===========================================================================
# 验收条件 4：成功响应约定（SPEC §9.3）
# ===========================================================================


class TestSuccessConventions:
    """验证成功响应直接返回资源，无 {code, message, data} 信封。"""

    def test_list_returns_no_envelope(self, client: TestClient) -> None:
        """列表响应不含 code/message/data 信封字段。"""
        response = client.get("/items")
        body = response.json()
        assert "code" not in body
        assert "message" not in body
        assert "data" not in body

    def test_create_returns_201_with_location(self, client: TestClient) -> None:
        """创建成功返回 201 和 Location 头。"""
        response = client.post("/items", json={"name": "delta", "quantity": 5})
        assert response.status_code == 201
        assert "location" in response.headers
        assert "/items/" in response.headers["location"]

    def test_create_returns_resource_directly(self, client: TestClient) -> None:
        """创建响应直接返回资源，无信封。"""
        response = client.post("/items", json={"name": "delta", "quantity": 5})
        body = response.json()
        assert "id" in body
        assert body["name"] == "delta"
        assert "code" not in body
        assert "data" not in body

    def test_delete_returns_204_no_body(self, client: TestClient) -> None:
        """无响应体的删除成功返回 204。"""
        response = client.delete("/items/1")
        assert response.status_code == 204
        # 204 响应无响应体
        assert response.content == b""

    def test_get_returns_resource_directly(self, client: TestClient) -> None:
        """普通成功响应直接返回资源（通过 Page.items），无信封。"""
        response = client.get("/items")
        assert response.status_code == 200
        body = response.json()
        # 直接返回资源列表在 items 中，无 code/message/data 包装
        assert isinstance(body["items"], list)
        assert len(body["items"]) > 0


# ===========================================================================
# 验收条件 5：文件/流式响应不使用 JSON 信封（SPEC §9.3）
# ===========================================================================


class TestFileStreamingResponse:
    """验证文件和流式响应不使用 JSON 信封。"""

    def test_download_returns_plain_text(self, client: TestClient) -> None:
        """文件下载返回原始文本内容，非 JSON 信封。"""
        response = client.get("/items/1/download")
        assert response.status_code == 200

        # Content-Type 不是 JSON
        content_type = response.headers["content-type"]
        assert "application/json" not in content_type
        assert "text/plain" in content_type

        # 响应体是原始内容，不是 JSON 信封
        assert response.text == "file-content-for-item-1"
        assert b"code" not in response.content
        assert b"message" not in response.content
        assert b"data" not in response.content


# ===========================================================================
# 验收条件 6：进程内任务工具（SPEC §20.1）
# ===========================================================================


class TestInProcessTaskRunner:
    """验证进程内轻量任务工具行为和文档约束（SPEC §20.1）。"""

    async def test_task_executes_successfully(self) -> None:
        """调度的任务成功执行。"""
        runner = InProcessTaskRunner()
        results: list[str] = []

        async def task_fn() -> None:
            results.append("done")

        runner.schedule(task_fn(), name="test-task")

        # 等待任务完成
        await runner.shutdown()
        assert results == ["done"]

    async def test_task_exception_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """任务异常记录到日志（SPEC §20.1）。"""
        runner = InProcessTaskRunner()

        async def failing_task() -> None:
            msg = "intentional test failure"
            raise RuntimeError(msg)

        with caplog.at_level(logging.ERROR, logger="app.tasks.background"):
            runner.schedule(failing_task(), name="failing-task")
            await runner.shutdown()

        # 验证异常被记录
        assert any("进程内任务异常" in record.message for record in caplog.records)

    async def test_task_cancelled_no_error_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """被取消的任务不记录异常。"""
        runner = InProcessTaskRunner()

        async def long_task() -> None:
            await asyncio.sleep(100)

        with caplog.at_level(logging.ERROR, logger="app.tasks.background"):
            task = runner.schedule(long_task(), name="long-task")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert not any("进程内任务异常" in record.message for record in caplog.records)

    async def test_shutdown_with_no_tasks(self) -> None:
        """无任务时 shutdown 不报错。"""
        runner = InProcessTaskRunner()
        await runner.shutdown()  # 不应抛出异常

    async def test_multiple_tasks_all_complete(self) -> None:
        """多个任务全部完成。"""
        runner = InProcessTaskRunner()
        counter: list[int] = []

        async def increment() -> None:
            counter.append(1)

        for i in range(5):
            runner.schedule(increment(), name=f"task-{i}")

        await runner.shutdown()
        assert len(counter) == 5

    def test_documentation_states_tasks_may_be_lost(self) -> None:
        """模块文档明确说明进程关闭时任务可能丢失（SPEC §20.1）。

        通过检查 InProcessTaskRunner 类和模块的文档字符串是否包含
        关于进程关闭时任务可能丢失的明确说明。
        """
        # 类文档字符串
        class_doc = InProcessTaskRunner.__doc__ or ""
        assert "丢失" in class_doc or "进程关闭" in class_doc

        # 模块文档字符串
        module_doc = inspect.getdoc(inspect.getmodule(InProcessTaskRunner)) or ""
        assert "丢失" in module_doc or "进程关闭" in module_doc

    def test_documentation_states_no_critical_tasks(self) -> None:
        """模块文档明确说明禁止用于关键业务操作（SPEC §20.1）。"""
        module_doc = inspect.getdoc(inspect.getmodule(InProcessTaskRunner)) or ""
        assert "禁止" in module_doc or "不可丢失" in module_doc


# ===========================================================================
# 额外验证：BaseRequestModel 强制 extra=forbid
# ===========================================================================


class TestBaseRequestModelEnforcesForbid:
    """验证 BaseRequestModel 基类设置了 extra=forbid。"""

    def test_base_request_model_config(self) -> None:
        """BaseRequestModel 的 model_config 中 extra 为 forbid。"""
        config = BaseRequestModel.model_config
        assert config.get("extra") == "forbid"

    def test_subclass_inherits_forbid(self) -> None:
        """BaseRequestModel 子类继承 extra=forbid。"""
        config = ItemCreate.model_config
        assert config.get("extra") == "forbid"


# ===========================================================================
# 额外验证：SortInstruction 不可变性
# ===========================================================================


class TestSortInstructionImmutable:
    """验证 SortInstruction 是不可变值对象。"""

    def test_frozen_dataclass(self) -> None:
        """SortInstruction 是 frozen dataclass，构造后不可变。"""
        instruction = SortInstruction(field="name", descending=False)
        with pytest.raises(AttributeError):
            instruction.field = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        """相同字段和方向的 SortInstruction 相等。"""
        a = SortInstruction(field="name", descending=True)
        b = SortInstruction(field="name", descending=True)
        c = SortInstruction(field="name", descending=False)
        assert a == b
        assert a != c
