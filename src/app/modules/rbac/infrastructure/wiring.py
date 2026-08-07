"""RBAC 模块服务装配工厂（SPEC §5.2、§5.5）。

提供 :class:`~app.modules.rbac.application.service.RbacService` 的
完整装配入口，以及 :class:`~app.modules.user.application.port.LastSuperAdminCheck`
的 RBAC 实现。

此模块是 Composition Root 的协作方——Composition Root 声明模块清单，
本模块提供从引擎到可用服务的具体装配逻辑。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.events.base import TransactionalEventHandlerFn
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.audit.infrastructure.audit_port import SqlAlchemyAuditPort
from app.modules.rbac.application.port import (
    RbacUnitOfWork,
)
from app.modules.rbac.application.service import RbacService
from app.modules.rbac.infrastructure.event_handlers import (
    handle_role_created,
    handle_role_disabled,
    handle_user_role_assigned,
    handle_user_role_removed,
)
from app.modules.rbac.infrastructure.unit_of_work import SqlAlchemyRbacUnitOfWork
from app.modules.registry import ModuleRegistry
from app.modules.user.application.port import LastSuperAdminCheck
from app.ports.audit import AuditPort


def create_rbac_service(
    engine: AsyncEngine,
    *,
    audit_port: AuditPort | None = None,
) -> RbacService:
    """从异步引擎装配完整的 RBAC 服务。

    Args:
        engine: SQLAlchemy 异步引擎
        audit_port: 审计端口（可选，默认新建 SqlAlchemyAuditPort）

    Returns:
        可用的 :class:`RbacService` 实例
    """
    # 延迟导入 MODULE 以打断循环依赖：
    # definition -> routes -> wiring -> definition
    from app.modules.rbac.definition import MODULE

    def uow_factory() -> RbacUnitOfWork:
        return SqlAlchemyRbacUnitOfWork(engine)

    resolved_audit_port = audit_port or SqlAlchemyAuditPort()

    module_registry = ModuleRegistry([MODULE])

    handler_implementations: dict[str, TransactionalEventHandlerFn] = {
        "rbac.handler.role_created": handle_role_created,
        "rbac.handler.role_disabled": handle_role_disabled,
        "rbac.handler.user_role_assigned": handle_user_role_assigned,
        "rbac.handler.user_role_removed": handle_user_role_removed,
    }
    event_registry = EventHandlerRegistry(module_registry, handler_implementations)
    event_dispatcher = TransactionalEventDispatcher(event_registry)

    return RbacService(
        uow_factory=uow_factory,
        event_dispatcher=event_dispatcher,
        audit_port=resolved_audit_port,
    )


class RbacLastSuperAdminCheck(LastSuperAdminCheck):
    """基于 RBAC 的最后一个超级管理员检查实现（SPEC §11.1、§13.4）。

    替换用户模块中的 :class:`NoOpLastSuperAdminCheck`，
    通过查询角色和用户-角色关系判断指定用户是否为系统最后一个可用超级管理员。

    "可用"指用户处于启用状态且拥有至少一个启用的超级管理员角色。
    超级管理员通过角色标志 ``is_super_admin`` 检测，不使用魔法用户 ID
    （SPEC §13.4）。

    Args:
        engine: SQLAlchemy 异步引擎
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def is_last_available_super_admin(self, user_id: object) -> bool:
        """判断指定用户是否为系统最后一个可用超级管理员。

        通过查询 user_roles → roles (is_super_admin=true, status='active')
        判断用户是否拥有超级管理员角色，以及是否还有其他可用超级管理员用户。

        Args:
            user_id: 待检查的用户 UUID

        Returns:
            是最后一个可用超级管理员返回 ``True``
        """
        from app.modules.rbac.infrastructure.unit_of_work import (
            SqlAlchemyRbacUnitOfWork,
        )
        from app.modules.user.application.port import UserRepository
        from app.modules.user.domain.model import UserStatus
        from app.modules.user.infrastructure.repository import (
            SqlAlchemyUserRepository,
        )

        uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))

        uow = SqlAlchemyRbacUnitOfWork(self._engine)
        try:
            async with uow:
                # 检查用户是否拥有超级管理员角色
                role_ids = await uow.user_roles.get_active_role_ids_for_user(uid)
                is_super = False
                for rid in role_ids:
                    role = await uow.roles.get_by_id(rid)
                    if role is not None and role.is_super_admin:
                        is_super = True
                        break

                if not is_super:
                    return False

                # 检查用户是否处于启用状态
                user_repo: UserRepository = SqlAlchemyUserRepository(uow.session)
                user = await user_repo.get_by_id(uid)
                if user is None or user.status is UserStatus.DISABLED:
                    return False

                # 检查是否还有其他可用的超级管理员用户
                super_admin_user_ids = await uow.user_roles.get_super_admin_user_ids()
                other_active_supers: list[UUID] = []
                for other_id in super_admin_user_ids:
                    if other_id == uid:
                        continue
                    other_user = await user_repo.get_by_id(other_id)
                    if other_user is not None and other_user.status is UserStatus.ACTIVE:
                        other_active_supers.append(other_id)

                return len(other_active_supers) == 0
        finally:
            pass
