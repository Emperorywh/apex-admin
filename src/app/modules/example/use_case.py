"""示例 Use Case — Application 层应用服务（SPEC 5.2 / 5.6 / 5.7）.

SPEC 5.6 事务管理:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
  - 最外层写 Use Case 负责开始、提交或回滚。
  - Router 只能获得 Use Case，不得获得 AsyncSession、Repository 或提交接口。

SPEC 5.7 事件:
  - 事务内事件处理器在当前 UoW 提交前同步执行。
  - 任一处理器失败时，整个 Use Case 回滚。

Use Case 在每个写方法中:
  1. 创建新 UoW（一个 Use Case 方法对应一个 UoW）。
  2. 从 UoW 的 session 构造 Repository Adapter。
  3. 执行业务逻辑。
  4. 收集事件并在 commit 前同步分发。
  5. 提交事务（异常时 ``__aexit__`` 自动回滚）。

SPEC 5.6: "Router 只能获得 Use Case"。Router 通过依赖注入获得此对象，
不直接接触 UoW、Repository 或 AsyncSession。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.api.pagination import SortField, total_pages
from app.core.events.dispatcher import TransactionalEventDispatcher
from app.modules.example.adapter import SqlAlchemyExampleItemRepository
from app.modules.example.errors import ExampleItemNotFoundError
from app.modules.example.events import ExampleItemCreated
from app.modules.example.models import ExampleItem
from app.modules.example.schemas import (
    ExampleItemCreateRequest,
    ExampleItemResponse,
    ExampleItemUpdateRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.context import UseCaseContext
    from app.application.ports import Clock, IdGenerator
    from app.core.events.handlers import TransactionalEventHandler
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.example.port import ExampleItemRepository


class ExampleItemUseCase:
    """示例条目 Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    Router 通过 FastAPI 依赖注入获得此实例。

    构造参数:
        uow_factory:    UoW 工厂，每次调用返回新 UoW
                        （``SqlAlchemyUnitOfWork`` 实例）。
        clock:          时钟 Port（SPEC 5.8）。
        id_generator:   标识生成器 Port（SPEC 5.8）。
        event_handlers: 事务内事件处理器列表（SPEC 5.7）。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        event_handlers: list[TransactionalEventHandler],
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._event_handlers = event_handlers

    def _create_repo(self, session: AsyncSession) -> ExampleItemRepository:
        """从 session 构造 Repository Adapter — SPEC 5.6.

        SPEC 5.6: "Repository Adapter 由 Composition Root 使用当前
        Unit of Work 拥有的 AsyncSession 构造"。
        """

        return SqlAlchemyExampleItemRepository(session)

    async def create_item(
        self,
        ctx: UseCaseContext,
        request: ExampleItemCreateRequest,
    ) -> ExampleItemResponse:
        """创建示例条目 — 写 Use Case（SPEC 5.6）.

        步骤:
          1. 开启 UoW（一个 Use Case 对应一个 UoW）。
          2. 构造 Repository，创建条目。
          3. 收集 ``EXAMPLE.ITEM_CREATED`` 事件。
          4. 在 commit 前同步分发事件（SPEC 5.7）。
          5. 提交事务。

        异常时 ``__aexit__`` 自动回滚，包括事件处理器失败的情况
        （SPEC 5.7: "任一事务内处理器失败时，整个 Use Case 回滚"）。
        """

        dispatcher = TransactionalEventDispatcher(self._event_handlers)
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            now = self._clock.now()
            item_id = self._id_generator.generate_id()

            item = ExampleItem(
                id=item_id,
                name=request.name,
                description=request.description,
                created_at=now,
                updated_at=now,
            )
            await repo.add(item)

            # 收集事件 — SPEC 5.7: 事件在 Use Case 执行过程中产生。
            dispatcher.collect(
                ExampleItemCreated(
                    code="EXAMPLE.ITEM_CREATED",
                    payload={"item_id": str(item_id), "name": request.name},
                    item_id=str(item_id),
                    name=request.name,
                ),
            )

            # commit 前同步分发 — SPEC 5.7
            await dispatcher.dispatch(uow.session)
            await uow.commit()

            return _to_response(item)

    async def get_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
    ) -> ExampleItemResponse:
        """查询单个条目 — 读操作（无需显式事务控制）."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            item = await repo.get_by_id(item_id)
            if item is None:
                raise ExampleItemNotFoundError(str(item_id))
            return _to_response(item)

    async def list_items(
        self,
        ctx: UseCaseContext,
        *,
        page: int,
        page_size: int,
        sort_fields: list[SortField],
    ) -> dict[str, object]:
        """分页查询条目列表 — SPEC 9.4.

        返回符合 SPEC 9.4 的分页响应结构 ``{items, total, page, page_size, pages}``。
        """

        offset = (page - 1) * page_size
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            items, total = await repo.list_items(
                offset=offset,
                limit=page_size,
                sort_fields=sort_fields,
            )

            return {
                "items": [_to_response(item) for item in items],
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": total_pages(total, page_size),
            }

    async def update_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
        request: ExampleItemUpdateRequest,
    ) -> ExampleItemResponse:
        """更新条目 — 写 Use Case（SPEC 5.6）."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            existing = await repo.get_by_id(item_id)
            if existing is None:
                raise ExampleItemNotFoundError(str(item_id))

            updated = ExampleItem(
                id=existing.id,
                name=request.name,
                description=request.description,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
            )
            await repo.save(updated)
            await uow.commit()

            return _to_response(updated)

    async def delete_item(
        self,
        ctx: UseCaseContext,
        item_id: UUID,
    ) -> None:
        """删除条目 — 写 Use Case（SPEC 5.6）.

        SPEC 9.3: "无响应体的删除成功返回 HTTP 204"。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            deleted = await repo.delete_by_id(item_id)
            if not deleted:
                raise ExampleItemNotFoundError(str(item_id))
            await uow.commit()


def _to_response(item: ExampleItem) -> ExampleItemResponse:
    """领域实体 → 响应 Schema 转换 — SPEC 5.2 职责分离."""

    return ExampleItemResponse(
        id=item.id,
        name=item.name,
        description=item.description,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
