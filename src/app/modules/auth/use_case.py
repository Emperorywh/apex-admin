"""认证 Use Case — Application 层应用服务（SPEC 5.2 / 5.6 / 12.1 / 12.3 / 12.4 / 18.1）.

SPEC 5.6 事务管理:
  - 一个最外层写 Use Case 对应一个 Unit of Work 和一个 AsyncSession。
  - 最外层写 Use Case 负责开始、提交或回滚。

SPEC 12.1 账号密码认证:
  - 登录前检查用户状态。
  - 登录成功创建服务端会话。
  - 登录成功时使用 check_needs_rehash 判断并在同一事务中升级旧参数哈希。
  - 登录失败记录安全事件。

SPEC 12.4 登录安全:
  - 防止通过错误响应枚举有效用户。
  - 用户不存在时执行虚拟哈希校验。
  - 账号与可信客户端 IP 双维度失败限制。
  - 限制响应与密码错误响应一致。

SPEC 12.3 会话管理:
  - 每个受保护请求使用 Access Token 摘要查询 PostgreSQL。
  - 校验用户启用、会话有效、Token 有效、空闲过期和绝对过期。
  - 最近活动时间最多每 5 分钟条件更新一次。

SPEC 18.1 登录日志:
  - 记录登录成功、失败、退出。
  - 记录失败原因分类。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.core.api.pagination import total_pages
from app.core.security.password import DUMMY_PASSWORD_HASH, Argon2Hasher
from app.core.security.token import generate_token
from app.modules.audit.models import LoginLogEntry
from app.modules.auth.adapter import (
    SqlAlchemyLoginAttemptRepository,
    SqlAlchemySessionRepository,
)
from app.modules.auth.constants import (
    ACCESS_TOKEN_TTL,
    ACCOUNT_FAILURE_LIMIT,
    ACTIVITY_UPDATE_INTERVAL,
    DIMENSION_ACCOUNT,
    DIMENSION_IP,
    FAILURE_LOCK_DURATION,
    IP_FAILURE_LIMIT,
    SESSION_ABSOLUTE_TIMEOUT,
    SESSION_IDLE_TIMEOUT,
)
from app.modules.auth.errors import InvalidCredentialsError
from app.modules.auth.models import Session
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    SessionResponse,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.application.ports import Clock, IdGenerator
    from app.core.security.digest import TokenDigestService
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.port import LoginLogPort, SecurityLogPort
    from app.modules.auth.port import LoginAttemptRepository, SessionRepository
    from app.modules.identity.port import UserAuthPort


# ── 登录失败原因分类 — SPEC 18.1 ─────────────────────────────────────────────

FAILURE_REASON_USER_NOT_FOUND = "user_not_found"
FAILURE_REASON_WRONG_PASSWORD = "wrong_password"
FAILURE_REASON_USER_DISABLED = "user_disabled"
FAILURE_REASON_ACCOUNT_LOCKED = "account_locked"
FAILURE_REASON_IP_LOCKED = "ip_locked"


class AuthUseCase:
    """认证 Use Case — 登录、退出与会话管理.

    SPEC 5.2 / 5.6: Use Case 是最外层写操作的入口，控制事务边界。
    SPEC 12.1 / 12.3 / 12.4: 实现登录安全策略和会话管理。

    构造参数:
        uow_factory:          UoW 工厂。
        clock:                时钟 Port。
        id_generator:         标识生成器 Port。
        hasher:               Argon2id 密码哈希服务。
        digest_service:       Token HMAC-SHA-256 摘要服务。
        user_auth_port_factory: 用户认证信息 Port 工厂（从 AsyncSession 构造）。
        login_log_factory:    登录日志 Port 工厂。
        security_log_factory: 安全日志 Port 工厂。
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        clock: Clock,
        id_generator: IdGenerator,
        hasher: Argon2Hasher,
        digest_service: TokenDigestService,
        user_auth_port_factory: Callable[[AsyncSession], UserAuthPort],
        login_log_factory: Callable[[AsyncSession], LoginLogPort],
        security_log_factory: Callable[[AsyncSession], SecurityLogPort],
    ) -> None:
        """初始化 Use Case，注入所有依赖."""

        self._uow_factory = uow_factory
        self._clock = clock
        self._id_generator = id_generator
        self._hasher = hasher
        self._digest_service = digest_service
        self._user_auth_port_factory = user_auth_port_factory
        self._login_log_factory = login_log_factory
        self._security_log_factory = security_log_factory

    def _create_session_repo(self, session: AsyncSession) -> SessionRepository:
        """从 session 构造会话 Repository Adapter — SPEC 5.6."""

        return SqlAlchemySessionRepository(session)

    def _create_attempt_repo(self, session: AsyncSession) -> LoginAttemptRepository:
        """从 session 构造登录失败计数 Repository Adapter."""

        return SqlAlchemyLoginAttemptRepository(session)

    def _create_user_auth_port(self, session: AsyncSession) -> UserAuthPort:
        """从 session 构造用户认证信息 Port — SPEC 5.2 跨模块."""

        return self._user_auth_port_factory(session)

    def _create_login_log(self, session: AsyncSession) -> LoginLogPort:
        """从 session 构造登录日志 Port — SPEC 18.1."""

        return self._login_log_factory(session)

    def _create_security_log(self, session: AsyncSession) -> SecurityLogPort:
        """从 session 构造安全日志 Port — SPEC 5.7."""

        return self._security_log_factory(session)

    # ── 登录 ────────────────────────────────────────────────────────────────

    async def login(
        self,
        request: LoginRequest,
        *,
        ip_address: str,
        user_agent: str | None,
        request_id: str,
    ) -> LoginResponse:
        """账号密码登录 — SPEC 12.1 / 12.4.

        登录流程:
          1. 检查账号和 IP 双维度失败限制（SPEC 12.4）。
          2. 查询用户；不存在则执行虚拟哈希校验（SPEC 12.4 防枚举）。
          3. 校验用户状态（SPEC 12.1: 登录前检查用户状态）。
          4. 验证密码。
          5. 成功: 清理账号维度失败计数，创建会话，生成 Token，
             rehash 升级（同事务），记录登录日志。
          6. 失败: 递增双维度计数，检查阈值，记录登录日志。

        SPEC 12.4: 所有失败路径返回完全一致的响应（``InvalidCredentialsError``），
        防止通过错误差异枚举有效用户。

        SPEC 12.1: 登录和刷新响应必须设置 ``Cache-Control: no-store``
        （由 Router 设置响应头）。

        参数:
            request:    登录请求。
            ip_address: 客户端 IP 地址。
            user_agent: User-Agent（可为空）。
            request_id: 请求标识。

        返回:
            登录成功响应（含 Access Token，仅返回一次）。

        抛出:
            InvalidCredentialsError: 所有登录失败场景。
        """

        now = self._clock.now()
        normalized_username = request.username.lower()

        async with self._uow_factory() as uow:
            session_repo = self._create_session_repo(uow.session)
            attempt_repo = self._create_attempt_repo(uow.session)
            user_auth = self._create_user_auth_port(uow.session)
            login_log = self._create_login_log(uow.session)
            security_log = self._create_security_log(uow.session)

            # ── 1. 检查双维度失败限制 — SPEC 12.4 ──
            account_attempt = await attempt_repo.get(
                DIMENSION_ACCOUNT,
                normalized_username,
            )
            ip_attempt = await attempt_repo.get(DIMENSION_IP, ip_address)

            # 检查账号维度锁定
            if self._is_locked(account_attempt, now):
                # SPEC 12.4: 限制响应与密码错误响应一致
                self._log_security_event(
                    security_log,
                    event_type="login_blocked",
                    username=request.username,
                    ip_address=ip_address,
                    reason=FAILURE_REASON_ACCOUNT_LOCKED,
                    request_id=request_id,
                )
                await self._record_login_log(
                    login_log,
                    user_id=None,
                    username=request.username,
                    session_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    result="failure",
                    failure_reason=FAILURE_REASON_ACCOUNT_LOCKED,
                    now=now,
                )
                # 需要提交登录日志（登录日志与安全日志独立）
                await uow.commit()
                raise InvalidCredentialsError("用户名或密码不正确")

            # 检查 IP 维度锁定
            if self._is_locked(ip_attempt, now):
                self._log_security_event(
                    security_log,
                    event_type="login_blocked",
                    username=request.username,
                    ip_address=ip_address,
                    reason=FAILURE_REASON_IP_LOCKED,
                    request_id=request_id,
                )
                await self._record_login_log(
                    login_log,
                    user_id=None,
                    username=request.username,
                    session_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    result="failure",
                    failure_reason=FAILURE_REASON_IP_LOCKED,
                    now=now,
                )
                await uow.commit()
                raise InvalidCredentialsError("用户名或密码不正确")

            # ── 2. 查询用户 — SPEC 12.4 防枚举 ──
            user_info = await user_auth.get_auth_info_by_username(request.username)

            if user_info is None:
                # SPEC 12.4: 用户不存在时执行虚拟哈希校验，
                # 消耗与真实验证相同的 CPU 时间。
                self._hasher.verify(DUMMY_PASSWORD_HASH, request.password)

                # 记录失败计数和日志
                await self._record_failure(
                    attempt_repo,
                    normalized_username,
                    ip_address,
                    now,
                )
                await self._record_login_log(
                    login_log,
                    user_id=None,
                    username=request.username,
                    session_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    result="failure",
                    failure_reason=FAILURE_REASON_USER_NOT_FOUND,
                    now=now,
                )
                self._log_security_event(
                    security_log,
                    event_type="login_failure",
                    username=request.username,
                    ip_address=ip_address,
                    reason=FAILURE_REASON_USER_NOT_FOUND,
                    request_id=request_id,
                )
                await uow.commit()
                raise InvalidCredentialsError("用户名或密码不正确")

            # ── 3. 检查用户状态 — SPEC 12.1 ──
            from app.modules.identity.models import UserStatus

            if user_info.status == UserStatus.DISABLED:
                await self._record_failure(
                    attempt_repo,
                    normalized_username,
                    ip_address,
                    now,
                )
                await self._record_login_log(
                    login_log,
                    user_id=str(user_info.id),
                    username=request.username,
                    session_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    result="failure",
                    failure_reason=FAILURE_REASON_USER_DISABLED,
                    now=now,
                )
                self._log_security_event(
                    security_log,
                    event_type="login_failure",
                    username=request.username,
                    ip_address=ip_address,
                    reason=FAILURE_REASON_USER_DISABLED,
                    request_id=request_id,
                    actor_id=str(user_info.id),
                )
                await uow.commit()
                raise InvalidCredentialsError("用户名或密码不正确")

            # ── 4. 验证密码 — SPEC 12.1 ──
            if not self._hasher.verify(user_info.password_hash, request.password):
                await self._record_failure(
                    attempt_repo,
                    normalized_username,
                    ip_address,
                    now,
                )
                await self._record_login_log(
                    login_log,
                    user_id=str(user_info.id),
                    username=request.username,
                    session_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    result="failure",
                    failure_reason=FAILURE_REASON_WRONG_PASSWORD,
                    now=now,
                )
                self._log_security_event(
                    security_log,
                    event_type="login_failure",
                    username=request.username,
                    ip_address=ip_address,
                    reason=FAILURE_REASON_WRONG_PASSWORD,
                    request_id=request_id,
                    actor_id=str(user_info.id),
                )
                await uow.commit()
                raise InvalidCredentialsError("用户名或密码不正确")

            # ── 5. 登录成功 ──

            # SPEC 12.4: 成功登录清理账号维度失败状态（不清理 IP 维度）
            await attempt_repo.reset(DIMENSION_ACCOUNT, normalized_username)

            # 生成不透明 Access Token — SPEC 12.1
            raw_token = generate_token()
            token_digest = self._digest_service.digest_access_token(raw_token)

            # 创建会话
            session_id = self._id_generator.generate_id()
            new_session = Session(
                id=session_id,
                user_id=user_info.id,
                access_token_digest=token_digest,
                device=request.device,
                ip_address=ip_address,
                user_agent=user_agent,
                created_at=now,
                last_activity_at=now,
                absolute_expires_at=now + SESSION_ABSOLUTE_TIMEOUT,
                token_expires_at=now + ACCESS_TOKEN_TTL,
                revoked=False,
                revoked_reason=None,
            )
            await session_repo.add(new_session)

            # SPEC 12.1: rehash 升级（同事务）
            new_hash: str | None = None
            if self._hasher.needs_rehash(user_info.password_hash):
                new_hash = self._hasher.hash(request.password)

            # 更新用户登录状态（同事务）— SPEC 12.1
            await user_auth.update_login_state(
                user_info.id,
                last_login_at=now,
                new_password_hash=new_hash,
            )

            # 记录登录成功日志 — SPEC 18.1
            await self._record_login_log(
                login_log,
                user_id=str(user_info.id),
                username=request.username,
                session_id=str(session_id),
                ip_address=ip_address,
                user_agent=user_agent,
                result="success",
                failure_reason=None,
                now=now,
            )

            await uow.commit()

            return LoginResponse(
                access_token=raw_token,
                token_type="Bearer",
                expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
            )

    # ── 退出登录 ─────────────────────────────────────────────────────────────

    async def logout_current(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        ip_address: str,
        user_agent: str | None,
        request_id: str,
    ) -> LogoutResponse:
        """退出当前会话 — SPEC 12.3.

        SPEC 12.3: "用户可以退出当前会话"。
        仅吊销当前会话，不影响其他会话。

        SPEC 18.1: 记录退出登录。

        参数:
            session_id: 当前会话 ID。
            user_id:    当前用户 ID。
            ip_address: 客户端 IP。
            user_agent: User-Agent。
            request_id: 请求标识。

        返回:
            退出响应（revoked_count=1）。
        """

        now = self._clock.now()
        async with self._uow_factory() as uow:
            repo = self._create_session_repo(uow.session)
            login_log = self._create_login_log(uow.session)

            revoked = await repo.revoke(session_id, reason="user_logout")

            if revoked:
                await self._record_login_log(
                    login_log,
                    user_id=str(user_id),
                    username=str(user_id),
                    session_id=str(session_id),
                    ip_address=ip_address,
                    user_agent=user_agent,
                    result="logout",
                    failure_reason=None,
                    now=now,
                )

            await uow.commit()
            return LogoutResponse(revoked_count=1 if revoked else 0)

    async def logout_other(
        self,
        *,
        current_session_id: UUID,
        user_id: UUID,
        ip_address: str,
        user_agent: str | None,
        request_id: str,
    ) -> LogoutResponse:
        """退出其他会话 — SPEC 12.3.

        SPEC 12.3: "用户可以退出其他会话"。
        吊销除当前会话外的所有活动会话。

        参数:
            current_session_id: 当前会话 ID（保留）。
            user_id:            当前用户 ID。
            ip_address:         客户端 IP。
            user_agent:         User-Agent。
            request_id:         请求标识。

        返回:
            退出响应（revoked_count 为被吊销的其他会话数量）。
        """

        now = self._clock.now()
        async with self._uow_factory() as uow:
            repo = self._create_session_repo(uow.session)
            login_log = self._create_login_log(uow.session)

            # 查询所有活动会话，逐个吊销非当前会话
            sessions = await repo.list_active_by_user(user_id)
            count = 0
            for s in sessions:
                if s.id != current_session_id:
                    await repo.revoke(s.id, reason="user_logout_other")
                    count += 1

            if count > 0:
                await self._record_login_log(
                    login_log,
                    user_id=str(user_id),
                    username=str(user_id),
                    session_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    result="logout",
                    failure_reason=None,
                    now=now,
                )

            await uow.commit()
            return LogoutResponse(revoked_count=count)

    # ── 会话查看 ─────────────────────────────────────────────────────────────

    async def list_sessions(
        self,
        *,
        user_id: UUID,
    ) -> dict[str, object]:
        """查询活动会话列表 — SPEC 12.3.

        SPEC 12.3: "用户可以查看自己的活动会话"。
        仅返回当前用户的活动（未吊销）会话。

        参数:
            user_id: 当前用户 ID。

        返回:
            分页格式响应 ``{items, total, page, page_size, pages}``。
        """

        async with self._uow_factory() as uow:
            repo = self._create_session_repo(uow.session)
            sessions = await repo.list_active_by_user(user_id)
            items = [_to_session_response(s) for s in sessions]
            total = len(items)
            return {
                "items": items,
                "total": total,
                "page": 1,
                "page_size": max(total, 1),
                "pages": total_pages(total, max(total, 1)),
            }

    # ── 认证依赖核心逻辑 ─────────────────────────────────────────────────────

    async def authenticate(
        self,
        raw_token: str,
    ) -> tuple[UUID, UUID] | None:
        """认证依赖核心逻辑 — SPEC 12.3.

        SPEC 12.3: "每个受保护请求都使用 Access Token 摘要查询 PostgreSQL，
        并校验用户启用、会话有效、Token 有效、空闲过期和绝对过期"。

        此方法由认证依赖（``dependencies.py``）调用，返回认证结果。
        失败时返回 None，调用方据此抛出 ``AuthenticationError``。

        SPEC 12.3: "最近活动时间最多每 5 分钟条件更新一次"。

        参数:
            raw_token: 明文 Access Token。

        返回:
            (user_id, session_id) 元组；认证失败返回 None。
        """

        token_digest = self._digest_service.digest_access_token(raw_token)

        async with self._uow_factory() as uow:
            repo = self._create_session_repo(uow.session)
            user_auth = self._create_user_auth_port(uow.session)

            session = await repo.get_by_token_digest(token_digest)
            if session is None:
                return None

            now = self._clock.now()

            # 校验会话有效 — SPEC 12.3
            if session.revoked:
                return None

            # 校验 Token 有效（15 分钟）— SPEC 12.1
            if now >= session.token_expires_at:
                return None

            # 校验空闲过期（30 分钟）— SPEC 12.3
            idle_cutoff = now - SESSION_IDLE_TIMEOUT
            if session.last_activity_at < idle_cutoff:
                return None

            # 校验绝对过期（12 小时）— SPEC 12.3
            if now >= session.absolute_expires_at:
                return None

            # 校验用户启用状态 — SPEC 12.3
            from app.modules.identity.models import UserStatus

            status = await user_auth.get_status_by_id(session.user_id)
            if status is None or status == UserStatus.DISABLED:
                return None

            # 条件更新最近活动时间 — SPEC 12.3
            # 最近活动时间 5 分钟内不重复写库
            activity_cutoff = now - ACTIVITY_UPDATE_INTERVAL
            if session.last_activity_at < activity_cutoff:
                await repo.update_activity(
                    session.id,
                    last_activity_at=now,
                )

            await uow.commit()
            return (session.user_id, session.id)

    # ── 内部辅助方法 ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_locked(
        attempt: object | None,
        now: datetime,
    ) -> bool:
        """检查指定维度的失败计数是否处于锁定状态 — SPEC 12.4."""

        if attempt is None:
            return False
        locked_until: object | None = getattr(attempt, "locked_until", None)
        if locked_until is None:
            return False
        assert isinstance(locked_until, datetime)
        return locked_until > now

    async def _record_failure(
        self,
        attempt_repo: LoginAttemptRepository,
        normalized_username: str,
        ip_address: str,
        now: object,
    ) -> None:
        """记录双维度失败并检查阈值 — SPEC 12.4.

        SPEC 12.4:
          - 账号连续失败 5 次限制 15 分钟。
          - IP 连续失败 20 次限制 15 分钟。

        先递增计数，达到阈值时设置锁定（``lock`` 与 ``record_failure`` 分离，
        避免重复递增）。
        """

        from datetime import datetime

        assert isinstance(now, datetime)
        lock_until = now + FAILURE_LOCK_DURATION

        # 账号维度
        account_count = await attempt_repo.record_failure(
            DIMENSION_ACCOUNT,
            normalized_username,
            failed_at=now,
        )
        if account_count >= ACCOUNT_FAILURE_LIMIT:
            await attempt_repo.lock(
                DIMENSION_ACCOUNT,
                normalized_username,
                locked_until=lock_until,
            )

        # IP 维度
        ip_count = await attempt_repo.record_failure(
            DIMENSION_IP,
            ip_address,
            failed_at=now,
        )
        if ip_count >= IP_FAILURE_LIMIT:
            await attempt_repo.lock(
                DIMENSION_IP,
                ip_address,
                locked_until=lock_until,
            )

    @staticmethod
    def _log_security_event(
        security_log: SecurityLogPort,
        *,
        event_type: str,
        username: str,
        ip_address: str,
        reason: str,
        request_id: str,
        actor_id: str | None = None,
    ) -> None:
        """记录安全事件到独立安全日志 — SPEC 5.7 / 12.4.

        SPEC 5.7: 失败操作记录到独立安全日志，不参与业务事务。
        SPEC 12.4 / 18.1: 不记录明文密码和完整 Token。
        """

        from datetime import UTC, datetime

        from app.modules.audit.models import SecurityEvent

        event = SecurityEvent(
            event_type=event_type,
            actor_id=actor_id,
            module="auth",
            action=f"auth.login.{event_type}",
            resource_type="user",
            resource_id=actor_id,
            request_id=request_id,
            ip_address=ip_address,
            failure_reason=f"{reason} (username={username})",
            occurred_at=datetime.now(UTC),
        )
        security_log.log_security_event(event)

    @staticmethod
    async def _record_login_log(
        login_log: LoginLogPort,
        *,
        user_id: str | None,
        username: str,
        session_id: str | None,
        ip_address: str,
        user_agent: str | None,
        result: str,
        failure_reason: str | None,
        now: object,
    ) -> None:
        """记录登录日志 — SPEC 18.1."""

        from datetime import datetime

        assert isinstance(now, datetime)
        from uuid import uuid4

        entry = LoginLogEntry(
            id=uuid4(),
            user_id=user_id,
            username=username,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
            result=result,
            failure_reason=failure_reason,
            occurred_at=now,
        )
        await login_log.record_login(entry)


# ── 领域实体 → 响应 Schema 转换 ──────────────────────────────────────────────


def _to_session_response(session: Session) -> SessionResponse:
    """会话领域实体 → 响应 Schema 转换 — SPEC 5.2 / 9.3.

    SPEC 9.3: "敏感字段不得进入响应模型"。
    ``access_token_digest`` 不包含在响应中。
    """

    return SessionResponse(
        id=session.id,
        device=session.device,
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        created_at=session.created_at,
        last_activity_at=session.last_activity_at,
        token_expires_at=session.token_expires_at,
    )
