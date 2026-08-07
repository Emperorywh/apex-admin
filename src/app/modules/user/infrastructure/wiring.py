"""用户模块服务装配工厂（SPEC §5.2、§5.5）。

提供 :class:`~app.modules.user.application.service.UserService` 的
完整装配入口，包含 UoW 工厂、密码哈希服务、超级管理员检查端口、
事件处理器注册表和事件调度器。

此模块是 Composition Root 的协作方——Composition Root 声明模块清单，
本模块提供从引擎到可用服务的具体装配逻辑。
Router 的依赖注入函数通过此工厂获取服务实例。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.events.base import TransactionalEventHandlerFn
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.registry import ModuleRegistry
from app.modules.user.application.port import (
    LastSuperAdminCheck,
    UserUnitOfWork,
)
from app.modules.user.application.service import UserService
from app.modules.user.domain.password import PasswordHasher
from app.modules.user.infrastructure.event_handlers import (
    handle_user_created,
    handle_user_disabled,
)
from app.modules.user.infrastructure.unit_of_work import SqlAlchemyUserUnitOfWork
from app.ports.session_lifecycle import SessionLifecyclePort


class NoOpLastSuperAdminCheck(LastSuperAdminCheck):
    """默认超级管理员检查实现（SPEC §11.1、§13.4）。

    在 RBAC 模块（TASK-018）实现前使用。始终返回 ``False``，
    不阻止任何禁用操作——因为系统尚未定义超级管理员角色。

    RBAC 模块实现后，Composition Root 将替换为查询角色和权限关系的
    真实实现。
    """

    async def is_last_available_super_admin(self, user_id: object) -> bool:  # noqa: ARG002
        """始终返回 ``False``——RBAC 实现前无超级管理员。"""
        return False


def create_user_service(
    engine: AsyncEngine,
    *,
    password_hasher: PasswordHasher | None = None,
    last_super_admin_check: LastSuperAdminCheck | None = None,
    session_lifecycle: SessionLifecyclePort | None = None,
) -> UserService:
    """从异步引擎装配完整的用户服务。

    装配步骤：
    1. 构造 UoW 工厂（每次调用返回新的 :class:`SqlAlchemyUserUnitOfWork`）
    2. 构造密码哈希服务（或使用传入实例）
    3. 构造超级管理员检查端口（或使用默认 NoOp 实现）
    4. 构造事件处理器注册表（从模块声明和处理器实现映射构建）
    5. 构造事件调度器
    6. 返回装配好的 :class:`UserService`

    Args:
        engine: SQLAlchemy 异步引擎
        password_hasher: 密码哈希服务实例（可选，默认新建）
        last_super_admin_check: 超级管理员检查端口（可选，默认 NoOp）
        session_lifecycle: 会话生命周期端口（可选，认证模块装配后注入）

    Returns:
        可用的 :class:`UserService` 实例
    """
    # 延迟导入 MODULE 以打断循环依赖：
    # definition -> routes -> wiring -> definition
    from app.modules.user.definition import MODULE

    def uow_factory() -> UserUnitOfWork:
        return SqlAlchemyUserUnitOfWork(engine)

    hasher = password_hasher or PasswordHasher()
    super_admin_check = last_super_admin_check or NoOpLastSuperAdminCheck()

    module_registry = ModuleRegistry([MODULE])

    handler_implementations: dict[str, TransactionalEventHandlerFn] = {
        "user.handler.created": handle_user_created,
        "user.handler.disabled": handle_user_disabled,
    }
    event_registry = EventHandlerRegistry(module_registry, handler_implementations)
    event_dispatcher = TransactionalEventDispatcher(event_registry)

    return UserService(
        uow_factory,
        hasher,
        super_admin_check,
        event_dispatcher,
        session_lifecycle,
    )
