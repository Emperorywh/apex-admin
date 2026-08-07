"""示例模块服务装配工厂（SPEC §5.2、§5.5）。

提供 :class:`~app.modules.example.application.service.ExampleService` 的
完整装配入口，包含 UoW 工厂、事件处理器注册表和事件调度器。

此模块是 Composition Root 的协作方——Composition Root 声明模块清单，
本模块提供从引擎到可用服务的具体装配逻辑。
Router 的依赖注入函数通过此工厂获取服务实例。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.events.base import TransactionalEventHandlerFn
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.example.application.port import ExampleUnitOfWork
from app.modules.example.application.service import ExampleService
from app.modules.example.infrastructure.event_handlers import handle_example_item_created
from app.modules.example.infrastructure.unit_of_work import SqlAlchemyExampleUnitOfWork
from app.modules.registry import ModuleRegistry


def create_example_service(engine: AsyncEngine) -> ExampleService:
    """从异步引擎装配完整的示例服务。

    装配步骤：
    1. 构造 UoW 工厂（每次调用返回新的 :class:`SqlAlchemyExampleUnitOfWork`）
    2. 构造事件处理器注册表（从模块声明和处理器实现映射构建）
    3. 构造事件调度器
    4. 返回装配好的 :class:`ExampleService`

    Args:
        engine: SQLAlchemy 异步引擎

    Returns:
        可用的 :class:`ExampleService` 实例
    """
    # 延迟导入 MODULE 以打断循环依赖：
    # definition -> routes -> wiring -> definition
    from app.modules.example.definition import MODULE

    def uow_factory() -> ExampleUnitOfWork:
        return SqlAlchemyExampleUnitOfWork(engine)

    module_registry = ModuleRegistry([MODULE])

    handler_implementations: dict[str, TransactionalEventHandlerFn] = {
        "example.handler.item_created": handle_example_item_created,
    }
    event_registry = EventHandlerRegistry(module_registry, handler_implementations)
    event_dispatcher = TransactionalEventDispatcher(event_registry)

    return ExampleService(uow_factory, event_dispatcher)
