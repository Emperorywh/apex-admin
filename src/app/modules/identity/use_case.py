"""用户 Use Case — Application 层应用服务（SPEC 5.2 / 5.6 / 5.7 / 11.1）.

SPEC 5.6 事务管理:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
  - 最外层写 Use Case 负责开始、提交或回滚。
  - Router 只能获得 Use Case，不得获得 AsyncSession、Repository 或提交接口。

SPEC 5.7 事件:
  - 事务内事件处理器在当前 UoW 提交前同步执行。
  - 任一处理器失败时，整个 Use Case 回滚。

SPEC 5.7 审计:
  - 成功操作的核心审计必须由 Use Case 显式调用审计 Port，
    并与业务事务共同提交（SPEC 5.7 / 18.2）。
  - 审计差异使用字段白名单生成（SPEC 18.2）。

SPEC 11.1 用户生命周期:
  - 创建、详情、分页、更新、启用、禁用、重置密码。
  - 自助查询/更新资料、自助改密。

SPEC 11.3 删除策略:
  - 已产生审计记录的用户物理删除被拒绝。
  - 默认优先采用禁用，而不是直接删除用户。

Use Case 在每个写方法中:
  1. 创建新 UoW（一个 Use Case 方法对应一个 UoW）。
  2. 从 UoW 的 session 构造 Repository Adapter 和审计 Adapter。
  3. 执行业务逻辑。
  4. 显式调用 AuditPort 写审计（同事务提交）。
  5. 收集事件并在 commit 前同步分发。
  6. 提交事务（异常时 ``__aexit__`` 自动回滚）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.api.pagination import SortField, total_pages
from app.core.events.dispatcher import TransactionalEventDispatcher
from app.core.security.password import Argon2Hasher, validate_password_length
from app.modules.audit.diff import FieldWhitelist, generate_diff
from app.modules.audit.models import AuditEntry
from app.modules.identity.adapter import SqlAlchemyUserRepository
from app.modules.identity.errors import (
    UserAlreadyActiveError,
    UserAlreadyDisabledError,
    UserHasAuditRecordsError,
    UserInvalidOldPasswordError,
    UserNotFoundError,
)
from app.modules.identity.events import PasswordResetByAdmin, UserDisabled
from app.modules.identity.models import User, UserStatus
from app.modules.identity.schemas import (
    SelfChangePasswordRequest,
    SelfProfileUpdateRequest,
    UserCreateRequest,
    UserResetPasswordRequest,
    UserResponse,
    UserUpdateRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.context import UseCaseContext
    from app.application.ports import Clock, IdGenerator
    from app.core.events.handlers import TransactionalEventHandler
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.models import ChangeDiff
    from app.modules.audit.port import AuditPort
    from app.modules.identity.port import UserRepository

# ── 用户审计字段白名单 — SPEC 18.2 ──────────────────────────────────────────
#
# SPEC 18.2: "审计差异使用字段白名单生成，禁止对任意对象执行
# 反射式全字段序列化"。
# SPEC 18.2: "密码、Token、密钥等敏感字段不得进入差异内容"。
#
# ``password_hash`` 不在白名单中，且 FieldWhitelist 构造时会拒绝
# 包含 "password" 子串的字段名，从源头杜绝泄露。

USER_FIELD_WHITELIST = FieldWhitelist(
    module="identity",
    resource_type="user",
    fields=frozenset({"display_name", "status", "phone", "email"}),
)


class UserUseCase:
    """用户 Use Case — Application 层应用服务.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    Router 通过 FastAPI 依赖注入获得此实例。

    SPEC 5.7: 成功操作的审计通过 ``AuditPort`` 显式调用，
    与业务事务共同提交。禁用和重置密码发布事务内事件，
    auth 模块（TASK-013）注册处理器吊销会话。

    构造参数:
        uow_factory:    UoW 工厂，每次调用返回新 UoW。
        clock:          时钟 Port（SPEC 5.8）。
        id_generator:   标识生成器 Port（SPEC 5.8）。
        hasher:         Argon2id 密码哈希服务（SPEC 12.1）。
        event_handlers: 事务内事件处理器列表（SPEC 5.7）。
        audit_factory:  审计 Port 工厂——从 AsyncSession 构造 AuditPort，
                        由 Composition Root 注入避免跨模块依赖 Adapter。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        hasher: Argon2Hasher,
        event_handlers: list[TransactionalEventHandler],
        audit_factory: Callable[[AsyncSession], AuditPort],
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._hasher = hasher
        self._event_handlers = event_handlers
        self._audit_factory = audit_factory

    def _create_repo(self, session: AsyncSession) -> UserRepository:
        """从 session 构造用户 Repository Adapter — SPEC 5.6."""

        return SqlAlchemyUserRepository(session)

    def _create_audit(self, session: AsyncSession) -> AuditPort:
        """从 session 构造审计 Port — SPEC 5.7 / 5.2.

        通过工厂注入避免 Use Case 直接依赖审计模块的 Adapter
        （SPEC 5.2: 跨模块调用通过公开接口完成）。
        """

        return self._audit_factory(session)

    def _make_audit_entry(
        self,
        ctx: UseCaseContext,
        *,
        action: str,
        resource_id: str | None,
        resource_display_name: str | None,
        diff: ChangeDiff | None = None,
    ) -> AuditEntry:
        """构造操作审计条目 — SPEC 18.2 / 5.7.

        SPEC 18.2: 操作者/目标显示名称按操作发生时快照保存。
        SPEC 5.7: 审计与业务事务共同提交。

        参数:
            ctx:                    用例上下文（提供 request_id 和 actor_id）。
            action:                 审计动作编码。
            resource_id:            目标资源标识。
            resource_display_name:  目标显示名快照。
            diff:                   变更差异（字段白名单生成）。
        """

        return AuditEntry(
            id=self._id_generator.generate_id(),
            actor_id=ctx.actor_id,
            actor_display_name=ctx.actor_id or "system",
            module="identity",
            action=action,
            resource_type="user",
            resource_id=resource_id,
            resource_display_name=resource_display_name,
            result="success",
            request_id=ctx.request_id or None,
            diff=diff,
            occurred_at=self._clock.now(),
        )

    @staticmethod
    def _user_state(user: User) -> dict[str, str | None]:
        """提取审计白名单字段状态 — SPEC 18.2.

        只提取白名单中的字段，用于生成变更差异。
        """

        return {
            "display_name": user.display_name,
            "status": user.status.value,
            "phone": user.phone,
            "email": user.email,
        }

    # ── 管理端生命周期 ──────────────────────────────────────────────────────

    async def create_user(
        self,
        ctx: UseCaseContext,
        request: UserCreateRequest,
    ) -> UserResponse:
        """创建用户 — 写 Use Case（SPEC 5.6 / 11.1）.

        步骤:
          1. 密码策略校验 + Argon2id 哈希（SPEC 23.2 / 12.1）。
          2. 开启 UoW，构造 Repository，创建用户。
          3. 写审计（同事务提交）。
          4. 提交事务。

        用户名冲突时由数据库唯一约束拦截，翻译为
        ``UserAlreadyExistsError``（SPEC 8.4）。
        """

        validate_password_length(request.password)
        password_hash = self._hasher.hash(request.password)

        dispatcher = TransactionalEventDispatcher(self._event_handlers)
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            now = self._clock.now()
            user_id = self._id_generator.generate_id()

            user = User(
                id=user_id,
                username=request.username,
                display_name=request.display_name,
                password_hash=password_hash,
                status=UserStatus.ACTIVE,
                phone=request.phone,
                email=request.email,
                last_login_at=None,
                password_updated_at=now,
                created_at=now,
                updated_at=now,
                created_by=ctx.actor_id,
                updated_by=ctx.actor_id,
            )
            await repo.add(user)

            # 审计 — SPEC 5.7 / 18.2: 创建操作写审计，同事务提交。
            # 创建无前状态，diff 只记录目标值。
            diff = generate_diff(
                USER_FIELD_WHITELIST,
                before=None,
                after=self._user_state(user),
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="identity.user.create",
                    resource_id=str(user_id),
                    resource_display_name=user.display_name,
                    diff=diff,
                ),
            )

            await dispatcher.dispatch(uow.session)
            await uow.commit()

            return _to_response(user)

    async def get_user(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
    ) -> UserResponse:
        """查询单个用户详情 — 读操作（无需显式事务控制）."""

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            user = await repo.get_by_id(user_id)
            if user is None:
                raise UserNotFoundError(str(user_id))
            return _to_response(user)

    async def list_users(
        self,
        ctx: UseCaseContext,
        *,
        page: int,
        page_size: int,
        sort_fields: list[SortField],
        status_filter: UserStatus | None = None,
    ) -> dict[str, object]:
        """分页查询用户列表 — SPEC 9.4.

        返回符合 SPEC 9.4 的分页响应结构 ``{items, total, page, page_size, pages}``。
        可选按状态筛选（SPEC 9.4: "筛选字段由具体模块显式声明"）。
        """

        offset = (page - 1) * page_size
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            users, total = await repo.list_users(
                offset=offset,
                limit=page_size,
                sort_fields=sort_fields,
                status_filter=status_filter,
            )

            return {
                "items": [_to_response(user) for user in users],
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": total_pages(total, page_size),
            }

    async def update_user(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
        request: UserUpdateRequest,
    ) -> UserResponse:
        """更新用户资料（管理端）— 写 Use Case（SPEC 5.6 / 11.1）.

        更新 display_name、phone、email 并写审计差异（字段白名单）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            existing = await repo.get_by_id(user_id)
            if existing is None:
                raise UserNotFoundError(str(user_id))

            before_state = self._user_state(existing)

            updated = User(
                id=existing.id,
                username=existing.username,
                display_name=request.display_name,
                password_hash=existing.password_hash,
                status=existing.status,
                phone=request.phone,
                email=request.email,
                last_login_at=existing.last_login_at,
                password_updated_at=existing.password_updated_at,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            after_state = self._user_state(updated)
            diff = generate_diff(
                USER_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="identity.user.update",
                    resource_id=str(user_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response(updated)

    async def enable_user(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
    ) -> UserResponse:
        """启用用户 — 写 Use Case（SPEC 5.6 / 11.1）.

        SPEC 11.1: "启用用户"。
        已启用用户再次启用返回 ``UserAlreadyActiveError``。
        状态变更通过 AuditPort 写审计（SPEC 18.2 / 5.7）。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            existing = await repo.get_by_id(user_id)
            if existing is None:
                raise UserNotFoundError(str(user_id))
            if existing.status == UserStatus.ACTIVE:
                raise UserAlreadyActiveError(str(user_id))

            before_state = self._user_state(existing)

            updated = User(
                id=existing.id,
                username=existing.username,
                display_name=existing.display_name,
                password_hash=existing.password_hash,
                status=UserStatus.ACTIVE,
                phone=existing.phone,
                email=existing.email,
                last_login_at=existing.last_login_at,
                password_updated_at=existing.password_updated_at,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            after_state = self._user_state(updated)
            diff = generate_diff(
                USER_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="identity.user.enable",
                    resource_id=str(user_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response(updated)

    async def disable_user(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
    ) -> UserResponse:
        """禁用用户 — 写 Use Case（SPEC 5.6 / 11.1 / 5.7）.

        SPEC 11.1: "禁用用户"。
        SPEC 11.3: "默认优先采用禁用或注销，而不是直接删除用户"。
        SPEC 5.7: 发布 ``USER.DISABLED`` 事件，auth 模块（TASK-013）注册
        事务内处理器吊销该用户的全部会话（SPEC 12.3）。

        已禁用用户再次禁用返回 ``UserAlreadyDisabledError``。
        """

        dispatcher = TransactionalEventDispatcher(self._event_handlers)
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            existing = await repo.get_by_id(user_id)
            if existing is None:
                raise UserNotFoundError(str(user_id))
            if existing.status == UserStatus.DISABLED:
                raise UserAlreadyDisabledError(str(user_id))

            before_state = self._user_state(existing)

            updated = User(
                id=existing.id,
                username=existing.username,
                display_name=existing.display_name,
                password_hash=existing.password_hash,
                status=UserStatus.DISABLED,
                phone=existing.phone,
                email=existing.email,
                last_login_at=existing.last_login_at,
                password_updated_at=existing.password_updated_at,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            # 发布 USER.DISABLED 事件 — SPEC 5.7
            # 载荷仅含稳定编码和资源 ID（user_id、user_status）。
            dispatcher.collect(
                UserDisabled(
                    code="USER.DISABLED",
                    payload={
                        "user_id": str(user_id),
                        "user_status": UserStatus.DISABLED.value,
                    },
                    user_id=str(user_id),
                    user_status=UserStatus.DISABLED.value,
                ),
            )

            after_state = self._user_state(updated)
            diff = generate_diff(
                USER_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="identity.user.disable",
                    resource_id=str(user_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            # commit 前同步分发事件 — SPEC 5.7
            await dispatcher.dispatch(uow.session)
            await uow.commit()
            return _to_response(updated)

    async def reset_password(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
        request: UserResetPasswordRequest,
    ) -> None:
        """管理员重置用户密码 — 写 Use Case（SPEC 5.6 / 11.1 / 5.7）.

        SPEC 11.1: "重置用户密码"。
        SPEC 5.7: 发布 ``USER.PASSWORD_RESET_BY_ADMIN`` 事件，
        auth 模块（TASK-013）注册事务内处理器吊销该用户全部会话
        （SPEC 12.3: "管理员重置密码后吊销该用户全部会话"）。

        管理员重置不需要旧密码，与自助改密不同。
        """

        validate_password_length(request.new_password)
        new_hash = self._hasher.hash(request.new_password)

        dispatcher = TransactionalEventDispatcher(self._event_handlers)
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            existing = await repo.get_by_id(user_id)
            if existing is None:
                raise UserNotFoundError(str(user_id))

            now = self._clock.now()
            updated = User(
                id=existing.id,
                username=existing.username,
                display_name=existing.display_name,
                password_hash=new_hash,
                status=existing.status,
                phone=existing.phone,
                email=existing.email,
                last_login_at=existing.last_login_at,
                password_updated_at=now,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            # 发布 USER.PASSWORD_RESET_BY_ADMIN 事件 — SPEC 5.7
            # 载荷仅含资源 ID（user_id），不含密码或哈希。
            dispatcher.collect(
                PasswordResetByAdmin(
                    code="USER.PASSWORD_RESET_BY_ADMIN",
                    payload={"user_id": str(user_id)},
                    user_id=str(user_id),
                ),
            )

            # 审计 — SPEC 18.2: 密码变更必须审计，
            # 但 password_hash 不在白名单中，diff 不含敏感字段。
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="identity.user.reset_password",
                    resource_id=str(user_id),
                    resource_display_name=updated.display_name,
                    diff=None,
                ),
            )

            await dispatcher.dispatch(uow.session)
            await uow.commit()

    async def delete_user(
        self,
        ctx: UseCaseContext,
        user_id: UUID,
    ) -> None:
        """物理删除用户 — 写 Use Case（SPEC 5.6 / 11.3）.

        SPEC 11.3 删除策略:
          - "已产生审计记录的用户不得因物理删除导致审计信息失真"。
          - "默认优先采用禁用或注销，而不是直接删除用户"。

        物理删除前检查该用户是否有审计记录。有审计记录时拒绝删除，
        返回 ``UserHasAuditRecordsError``。无审计记录时允许物理删除
        （但禁用是推荐操作）。

        SPEC 11.3: "用户名称发生变化时，历史审计记录仍能识别当时操作者"
        ——通过审计模块的显示名快照机制保证。
        """

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            existing = await repo.get_by_id(user_id)
            if existing is None:
                raise UserNotFoundError(str(user_id))

            # 检查该用户是否产生审计记录 — SPEC 11.3
            audit_count = await audit.count_by_resource("user", str(user_id))
            if audit_count > 0:
                raise UserHasAuditRecordsError(str(user_id))

            await repo.delete_by_id(user_id)
            await uow.commit()

    # ── 自助端点 ────────────────────────────────────────────────────────────

    async def get_self_profile(
        self,
        ctx: UseCaseContext,
    ) -> UserResponse:
        """自助查询个人资料 — SPEC 11.1.

        SPEC 11.1: "用户查询自己的资料"。
        当前用户 ID 从 ``UseCaseContext.actor_id`` 获取。
        未认证时（actor_id 为 None）由 Router 层返回 401。
        """

        assert ctx.actor_id is not None
        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            user = await repo.get_by_id(UUID(ctx.actor_id))
            if user is None:
                raise UserNotFoundError(ctx.actor_id)
            return _to_response(user)

    async def update_self_profile(
        self,
        ctx: UseCaseContext,
        request: SelfProfileUpdateRequest,
    ) -> UserResponse:
        """自助更新个人资料 — SPEC 11.1.

        SPEC 11.1: "用户更新允许自助修改的资料"。
        自助端点仅允许白名单字段（display_name、phone、email），
        通过 ``SelfProfileUpdateRequest`` 的 ``extra="forbid"`` 在
        Schema 层面拒绝其他字段（如 username、status）。

        状态变更写审计（同事务提交，SPEC 5.7 / 18.2）。
        """

        assert ctx.actor_id is not None
        user_id = UUID(ctx.actor_id)

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            existing = await repo.get_by_id(user_id)
            if existing is None:
                raise UserNotFoundError(ctx.actor_id)

            before_state = self._user_state(existing)

            updated = User(
                id=existing.id,
                username=existing.username,
                display_name=request.display_name,
                password_hash=existing.password_hash,
                status=existing.status,
                phone=request.phone,
                email=request.email,
                last_login_at=existing.last_login_at,
                password_updated_at=existing.password_updated_at,
                created_at=existing.created_at,
                updated_at=self._clock.now(),
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            after_state = self._user_state(updated)
            diff = generate_diff(
                USER_FIELD_WHITELIST,
                before=before_state,
                after=after_state,
            )
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="identity.user.self_update",
                    resource_id=str(user_id),
                    resource_display_name=updated.display_name,
                    diff=diff,
                ),
            )

            await uow.commit()
            return _to_response(updated)

    async def change_self_password(
        self,
        ctx: UseCaseContext,
        request: SelfChangePasswordRequest,
    ) -> None:
        """自助修改密码 — SPEC 11.1.

        SPEC 11.1: "用户修改自己的密码"。
        自助改密必须校验旧密码——通过 Argon2id verify 验证旧密码
        与存储哈希是否匹配（SPEC 12.1）。

        SPEC 12.3: "用户主动修改密码时保留当前会话并吊销其他会话"。
        会话吊销逻辑由 auth 模块（TASK-013）实现，本任务不发布事件
        （自助改密的会话处理策略与管理员重置不同）。
        """

        assert ctx.actor_id is not None
        user_id = UUID(ctx.actor_id)

        validate_password_length(request.new_password)

        async with self._uow_factory() as uow:
            repo = self._create_repo(uow.session)
            audit = self._create_audit(uow.session)
            existing = await repo.get_by_id(user_id)
            if existing is None:
                raise UserNotFoundError(ctx.actor_id)

            # 校验旧密码 — SPEC: 自助改密必须校验旧密码。
            if not self._hasher.verify(existing.password_hash, request.old_password):
                raise UserInvalidOldPasswordError(ctx.actor_id)

            new_hash = self._hasher.hash(request.new_password)
            now = self._clock.now()
            updated = User(
                id=existing.id,
                username=existing.username,
                display_name=existing.display_name,
                password_hash=new_hash,
                status=existing.status,
                phone=existing.phone,
                email=existing.email,
                last_login_at=existing.last_login_at,
                password_updated_at=now,
                created_at=existing.created_at,
                updated_at=now,
                created_by=existing.created_by,
                updated_by=ctx.actor_id,
            )
            await repo.save(updated)

            # 审计 — SPEC 18.2: 密码变更必须审计，
            # 但 password_hash 不在白名单中，diff 为空。
            await audit.record_audit(
                self._make_audit_entry(
                    ctx,
                    action="identity.user.self_change_password",
                    resource_id=str(user_id),
                    resource_display_name=updated.display_name,
                    diff=None,
                ),
            )

            await uow.commit()


def _to_response(user: User) -> UserResponse:
    """领域实体 → 响应 Schema 转换 — SPEC 5.2 职责分离.

    SPEC 9.3: "敏感字段不得进入响应模型"。
    ``password_hash`` 不包含在响应中（SPEC 23.2: "禁止记录和回显密码"）。
    """

    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status.value,
        phone=user.phone,
        email=user.email,
        last_login_at=user.last_login_at,
        password_updated_at=user.password_updated_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
