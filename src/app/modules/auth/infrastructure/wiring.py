"""认证模块服务装配工厂（SPEC §5.2、§5.5）。

提供 :class:`~app.modules.auth.application.service.AuthService` 的
完整装配入口，包含 UoW 工厂、密码哈希服务、Token 生成器、Token 摘要器、
事件处理器注册表和事件调度器。

Token HMAC 密钥从 :class:`~app.config.settings.Settings` 加载，
两个密钥独立且已通过 Settings 校验（SPEC §12.2：缺失、相同或长度不足时
启动失败）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.config.settings import Settings
from app.events.base import TransactionalEventHandlerFn
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.auth.application.port import AuthUnitOfWork
from app.modules.auth.application.service import AuthService
from app.modules.auth.domain.tokens import TokenDigester, TokenGenerator
from app.modules.auth.infrastructure.event_handlers import (
    handle_session_created,
    handle_session_revoked,
)
from app.modules.auth.infrastructure.unit_of_work import SqlAlchemyAuthUnitOfWork
from app.modules.registry import ModuleRegistry
from app.modules.user.domain.password import PasswordHasher


def create_auth_service(
    engine: AsyncEngine,
    settings: Settings,
) -> AuthService:
    """从异步引擎和配置装配完整的认证服务。

    装配步骤：
    1. 构造 UoW 工厂（每次调用返回新的 :class:`SqlAlchemyAuthUnitOfWork`）
    2. 构造密码哈希服务（复用用户模块的 Argon2id 实现）
    3. 构造 Token 生成器和 Token 摘要器（密钥从配置加载）
    4. 构造事件处理器注册表（从模块声明和处理器实现映射构建）
    5. 构造事件调度器
    6. 返回装配好的 :class:`AuthService`

    Args:
        engine: SQLAlchemy 异步引擎
        settings: 已校验的部署配置（提供 Token HMAC 密钥）

    Returns:
        可用的 :class:`AuthService` 实例
    """
    # 延迟导入 MODULE 以打断循环依赖：
    # definition -> routes -> wiring -> definition
    from app.modules.auth.definition import MODULE

    def uow_factory() -> AuthUnitOfWork:
        return SqlAlchemyAuthUnitOfWork(engine)

    password_hasher = PasswordHasher()
    token_generator = TokenGenerator()
    token_digester = TokenDigester(
        access_key=settings.access_token_hmac_key,
        refresh_key=settings.refresh_token_hmac_key,
    )

    module_registry = ModuleRegistry([MODULE])

    handler_implementations: dict[str, TransactionalEventHandlerFn] = {
        "auth.handler.session_created": handle_session_created,
        "auth.handler.session_revoked": handle_session_revoked,
    }
    event_registry = EventHandlerRegistry(module_registry, handler_implementations)
    event_dispatcher = TransactionalEventDispatcher(event_registry)

    return AuthService(
        uow_factory=uow_factory,
        password_hasher=password_hasher,
        token_generator=token_generator,
        token_digester=token_digester,
        event_dispatcher=event_dispatcher,
    )
