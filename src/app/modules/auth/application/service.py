"""认证模块应用服务 / Use Case（SPEC §5.2、§5.6、§5.7、§12.1、§12.2、§12.3）。

Use Case 编排密码验证、会话创建、Token 生成、Token 轮换、重放检测、
会话管理和在线校验：

登录流程（SPEC §12.1）：
1. 按用户名查询用户——不存在时执行固定 Argon2id 虚拟哈希校验
2. 检查用户状态（SPEC §12.1：登录前检查用户状态）
3. Argon2id 验证密码——失败时记录安全事件并返回认证错误
4. ``check_needs_rehash`` 在同一事务中升级旧参数哈希（SPEC §12.1）
5. 创建服务端会话（SPEC §12.3）
6. 生成 Access Token（HMAC-SHA-256 摘要入库，明文返回一次）
7. 生成 Refresh Token（独立密钥 HMAC-SHA-256 摘要入库，明文通过 Cookie 传递）

登出流程（SPEC §12.3、§12.4）：
1. 通过 Refresh Token 摘要查找会话
2. 吊销会话
3. Cookie 删除由路由层处理

刷新流程（SPEC §12.2）：
1. 计算 Refresh Token 摘要并 ``FOR UPDATE`` 锁定行
2. Token 已使用 → 重放检测：吊销整个 Session 和 Token Family
3. 旧 Refresh Token 标记为已使用，删除旧 Access Token
4. 生成新 Access Token + Refresh Token（同一事务）
5. 检查用户状态、会话有效性、空闲/绝对过期

在线校验（SPEC §12.3）：
1. Access Token 摘要查 DB
2. 检查用户启用、会话有效、Token 有效、空闲/绝对过期
3. 最近活动时间最多每 5 分钟条件更新一次

安全约束：
- 登录失败不区分用户不存在与密码错误（SPEC §12.4）
- 不在日志中记录明文密码或完整 Token（SPEC §12.4）
- 固定 Argon2id 虚拟哈希校验降低响应时间差（SPEC §12.4）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from app.errors import AuthenticationError, NotFoundError
from app.events.dispatcher import TransactionalEventDispatcher
from app.modules.auth.application.port import (
    AuthApplicationPort,
    AuthContext,
    AuthUnitOfWork,
    LoginResult,
    RefreshResult,
)
from app.modules.auth.domain.events import SessionCreated, SessionRevoked
from app.modules.auth.domain.login_security import (
    ACCOUNT_LOCK_THRESHOLD,
    IP_LOCK_THRESHOLD,
    LoginAttempt,
    LoginAttemptDimension,
)
from app.modules.auth.domain.model import (
    ACCESS_TOKEN_TTL_MINUTES,
    ACTIVITY_UPDATE_INTERVAL_MINUTES,
    REASON_ADMIN_FORCE_LOGOUT,
    REASON_LOGOUT,
    REASON_REPLAY_DETECTED,
    REASON_SESSION_EXPIRED,
    AccessTokenRecord,
    RefreshTokenRecord,
    Session,
)
from app.modules.auth.domain.tokens import TokenDigester, TokenGenerator
from app.modules.user.domain.model import UserStatus
from app.modules.user.domain.password import PasswordHasher

_logger = logging.getLogger("app.modules.auth.service")


class AuthService(AuthApplicationPort):
    """认证模块应用服务（SPEC §12.1、§12.2、§12.3）。

    实现登录、登出、刷新、在线校验和会话管理 Use Case。每个写 Use Case
    在独立的 Unit of Work 中执行，退出时统一提交或回滚。

    Args:
        uow_factory: 工作单元工厂，每次调用返回新的 :class:`AuthUnitOfWork`
        password_hasher: Argon2id 密码哈希服务（复用用户模块实现）
        token_generator: 不透明随机 Token 生成器
        token_digester: Token HMAC-SHA-256 摘要计算服务
        event_dispatcher: 事务内事件调度器
    """

    def __init__(
        self,
        uow_factory: Callable[[], AuthUnitOfWork],
        password_hasher: PasswordHasher,
        token_generator: TokenGenerator,
        token_digester: TokenDigester,
        event_dispatcher: TransactionalEventDispatcher,
    ) -> None:
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._token_generator = token_generator
        self._token_digester = token_digester
        self._event_dispatcher = event_dispatcher
        # 固定虚拟哈希——用户不存在时执行此哈希校验以均衡响应时间
        # （SPEC §12.4：降低响应时间差导致的账号枚举风险）
        self._dummy_hash = password_hasher.hash("__apex_dummy_hash_value__")

    # ------------------------------------------------------------------
    # 登录 Use Case（SPEC §12.1）
    # ------------------------------------------------------------------

    async def login(  # noqa: PLR0913
        self,
        *,
        username: str,
        password: str,
        ip: str,
        user_agent: str,
        device: str | None,
        current_time: datetime,
    ) -> LoginResult:
        """账号密码登录（SPEC §12.1、§12.4）。

        安全措施：
        - 暴力破解防护：账号 / IP 双维度检查（SPEC §12.4）
        - 用户不存在时执行虚拟哈希校验（SPEC §12.4）
        - 登录前检查用户状态（SPEC §12.1）
        - 登录失败记录安全事件并递增双维度计数（SPEC §12.1、§12.4）
        - ``check_needs_rehash`` 在同一事务中升级哈希（SPEC §12.1）
        - 明文 Token 绝不入库（SPEC §12.2）
        - 成功登录清理账号维度失败状态，不清理 IP 维度（SPEC §12.4）

        Raises:
            AuthenticationError: 用户名或密码不正确 / 用户已禁用 / 触发暴力破解限制
        """
        # 规范化账号标识——小写化以统一统计（SPEC §12.4：规范化账号标识）
        normalized_username = username.strip().lower()

        async with self._uow_factory() as uow:
            # 暴力破解防护——检查双维度限制（SPEC §12.4）
            # 限制时的响应与普通登录失败响应一致
            await self._check_brute_force_lock(
                uow=uow,
                normalized_username=normalized_username,
                ip=ip,
                current_time=current_time,
            )

            user = await uow.users.get_by_username(username)

            # 用户不存在 → 虚拟哈希校验（SPEC §12.4）
            if user is None:
                self._password_hasher.verify(self._dummy_hash, password)
                await self._record_brute_force_failure(
                    uow=uow,
                    normalized_username=normalized_username,
                    ip=ip,
                    current_time=current_time,
                )
                self._record_login_failure(
                    username=username,
                    ip=ip,
                    user_agent=user_agent,
                    reason="user_not_found",
                )
                raise AuthenticationError(
                    "用户名或密码不正确",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # 检查用户状态（SPEC §12.1：登录前检查用户状态）
            if user.status is UserStatus.DISABLED:
                # 仍然执行哈希校验以均衡时间
                self._password_hasher.verify(user.password_hash, password)
                await self._record_brute_force_failure(
                    uow=uow,
                    normalized_username=normalized_username,
                    ip=ip,
                    current_time=current_time,
                )
                self._record_login_failure(
                    username=username,
                    ip=ip,
                    user_agent=user_agent,
                    reason="user_disabled",
                )
                raise AuthenticationError(
                    "用户名或密码不正确",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # Argon2id 验证密码（SPEC §12.1）
            if not self._password_hasher.verify(user.password_hash, password):
                await self._record_brute_force_failure(
                    uow=uow,
                    normalized_username=normalized_username,
                    ip=ip,
                    current_time=current_time,
                )
                self._record_login_failure(
                    username=username,
                    ip=ip,
                    user_agent=user_agent,
                    reason="wrong_password",
                )
                raise AuthenticationError(
                    "用户名或密码不正确",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # check_needs_rehash → 同一事务中升级哈希（SPEC §12.1）
            if self._password_hasher.needs_rehash(user.password_hash):
                new_hash = self._password_hasher.hash(password)
                user = user.change_password(
                    password_hash=new_hash,
                    current_time=current_time,
                )
                await uow.users.update(user)

            # 更新最近登录时间
            user = user.record_login(login_time=current_time)
            await uow.users.update(user)

            # 成功登录清理账号维度失败状态（SPEC §12.4）
            # IP 维度不清理（SPEC §12.4：成功登录不清理）
            await uow.login_attempts.delete(
                LoginAttemptDimension.ACCOUNT,
                normalized_username,
            )

            # 创建服务端会话（SPEC §12.3）
            session = Session.new(
                user_id=user.id,
                ip=ip,
                user_agent=user_agent,
                device=device,
                current_time=current_time,
            )
            await uow.sessions.add(session)

            # 生成 Access Token（SPEC §12.1、§12.2）
            access_token_plaintext = self._token_generator.generate()
            access_digest = self._token_digester.access_digest(access_token_plaintext)
            access_record = AccessTokenRecord.new(
                digest=access_digest,
                session_id=session.id,
                user_id=user.id,
                created_at=current_time,
            )
            await uow.access_tokens.add(access_record)

            # 生成 Refresh Token（SPEC §12.1、§12.2）
            refresh_token_plaintext = self._token_generator.generate()
            refresh_digest = self._token_digester.refresh_digest(refresh_token_plaintext)
            refresh_record = RefreshTokenRecord.new(
                digest=refresh_digest,
                session_id=session.id,
                user_id=user.id,
                token_family_id=uuid4(),
                created_at=current_time,
                expires_at=session.absolute_expiry,
            )
            await uow.refresh_tokens.add(refresh_record)

            # 发布会话创建事件
            self._event_dispatcher.collect(
                SessionCreated(
                    occurred_at=current_time,
                    session_id=session.id,
                    user_id=user.id,
                )
            )
            await self._event_dispatcher.flush(uow)

            # 记录登录成功日志（SPEC §18.1：记录登录成功）
            self._record_login_success(
                username=username,
                user_id=user.id,
                session_id=session.id,
                ip=ip,
                user_agent=user_agent,
            )

            return LoginResult(
                access_token=access_token_plaintext,
                refresh_token=refresh_token_plaintext,
                session_id=session.id,
                user_id=user.id,
                access_token_expires_in=ACCESS_TOKEN_TTL_MINUTES * 60,
            )

    # ------------------------------------------------------------------
    # 登出 Use Case（SPEC §12.3、§12.4）
    # ------------------------------------------------------------------

    async def logout(self, *, refresh_token: str, current_time: datetime) -> None:
        """退出登录（SPEC §12.3、§12.4）。

        通过 Refresh Token 摘要查找会话并吊销。Token 无效或已吊销时
        静默成功（幂等语义），不泄露 Token 有效性信息。
        """
        async with self._uow_factory() as uow:
            refresh_digest = self._token_digester.refresh_digest(refresh_token)
            record = await uow.refresh_tokens.get_by_digest(refresh_digest)

            # Token 不存在或已吊销 → 幂等成功
            if record is None:
                return

            session = await uow.sessions.get_by_id(record.session_id)
            if session is None or session.is_revoked:
                return

            # 吊销会话（SPEC §12.3：Logout 吊销服务端会话）
            await self._revoke_session_internal(
                uow=uow,
                session=session,
                reason=REASON_LOGOUT,
                current_time=current_time,
            )

    # ------------------------------------------------------------------
    # 刷新 Use Case——Token 轮换与重放检测（SPEC §12.2）
    # ------------------------------------------------------------------

    async def refresh(
        self,
        *,
        refresh_token: str,
        current_time: datetime,
    ) -> RefreshResult:
        """刷新 Token——轮换并重放检测（SPEC §12.2）。

        流程：
        1. 计算 Refresh Token 摘要
        2. ``SELECT ... FOR UPDATE`` 锁定 Token 行（并发保护）
        3. Token 不存在 → 认证错误
        4. Token 已使用 → 重放检测：吊销整个 Session 和 Token Family
        5. Token 已吊销/过期 → 认证错误
        6. 检查会话有效性（活跃、空闲/绝对过期）
        7. 检查用户状态（启用）
        8. 标记旧 Refresh Token 已使用
        9. 删除旧 Access Token（旧 AT 立即失效）
        10. 生成新 Access Token + Refresh Token（同一事务）

        Raises:
            AuthenticationError: Token 无效、已过期、重放检测触发
        """
        async with self._uow_factory() as uow:
            refresh_digest = self._token_digester.refresh_digest(refresh_token)

            # FOR UPDATE 行锁——确保并发刷新串行化（SPEC §12.2）
            record = await uow.refresh_tokens.get_by_digest_for_update(refresh_digest)

            if record is None:
                raise AuthenticationError(
                    "Refresh Token 无效",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # 重放检测——已使用的 Token 再次出现（SPEC §12.2）
            if record.is_used:
                await self._handle_replay(uow, record, current_time)
                raise AuthenticationError(
                    "Refresh Token 已被使用，检测到重放",
                    code="AUTH.TOKEN_REPLAY",
                )

            # Token 已吊销
            if record.is_revoked:
                raise AuthenticationError(
                    "Refresh Token 已吊销",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # Token 已过期
            if record.is_expired(current_time=current_time):
                raise AuthenticationError(
                    "Refresh Token 已过期",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # 检查会话有效性（SPEC §12.2：刷新检查会话有效性）
            session = await uow.sessions.get_by_id(record.session_id)
            if session is None:
                raise AuthenticationError(
                    "会话不存在",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # 被吊销会话不可刷新（SPEC §12.2）
            if session.is_revoked:
                raise AuthenticationError(
                    "会话已吊销",
                    code="AUTH.SESSION_REVOKED",
                )

            # 空闲/绝对过期检查（SPEC §12.2）
            if session.is_expired(current_time=current_time):
                await self._revoke_session_internal(
                    uow=uow,
                    session=session,
                    reason=REASON_SESSION_EXPIRED,
                    current_time=current_time,
                )
                raise AuthenticationError(
                    "会话已过期",
                    code="AUTH.SESSION_EXPIRED",
                )

            # 检查用户状态（SPEC §12.2：刷新检查用户状态）
            user = await uow.users.get_by_id(record.user_id)
            if user is None or user.status is UserStatus.DISABLED:
                raise AuthenticationError(
                    "用户不可用",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # === 同一事务中完成轮换（SPEC §12.2）===

            # 1. 标记旧 Refresh Token 已使用
            used_record = record.mark_used(current_time=current_time)
            await uow.refresh_tokens.update(used_record)

            # 2. 删除旧 Access Token——旧 AT 立即失效（SPEC §12.2）
            await uow.access_tokens.delete_by_session(session.id)

            # 3. 生成新 Access Token
            new_access_plaintext = self._token_generator.generate()
            new_access_digest = self._token_digester.access_digest(new_access_plaintext)
            new_access_record = AccessTokenRecord.new(
                digest=new_access_digest,
                session_id=session.id,
                user_id=user.id,
                created_at=current_time,
            )
            await uow.access_tokens.add(new_access_record)

            # 4. 生成新 Refresh Token（同 Family，前驱为旧 Token）
            new_refresh_plaintext = self._token_generator.generate()
            new_refresh_digest = self._token_digester.refresh_digest(
                new_refresh_plaintext,
            )
            new_refresh_record = record.rotated(
                new_digest=new_refresh_digest,
                current_time=current_time,
                expires_at=session.absolute_expiry,
            )
            await uow.refresh_tokens.add(new_refresh_record)

            # 5. 条件更新最近活动时间（SPEC §12.3：最多每 5 分钟一次）
            await self._conditionally_touch_session(
                uow=uow,
                session=session,
                current_time=current_time,
            )

            return RefreshResult(
                access_token=new_access_plaintext,
                refresh_token=new_refresh_plaintext,
                session_id=session.id,
                user_id=user.id,
                access_token_expires_in=ACCESS_TOKEN_TTL_MINUTES * 60,
            )

    # ------------------------------------------------------------------
    # 每请求在线校验（SPEC §12.3）
    # ------------------------------------------------------------------

    async def validate_access_token(
        self,
        *,
        access_token: str,
        current_time: datetime,
    ) -> AuthContext:
        """每请求在线校验 Access Token（SPEC §12.3）。

        1. 计算 Access Token 摘要并查询数据库
        2. 检查用户启用状态
        3. 检查会话有效性（活跃、空闲/绝对过期）
        4. 检查 Token 有效性（未过期）
        5. 条件更新最近活动时间（最多每 5 分钟一次）

        Raises:
            AuthenticationError: Token 无效、用户禁用、会话无效或已过期
        """
        async with self._uow_factory() as uow:
            access_digest = self._token_digester.access_digest(access_token)
            token_record = await uow.access_tokens.get_by_digest(access_digest)

            if token_record is None:
                raise AuthenticationError(
                    "Access Token 无效",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # Token 过期检查
            if token_record.is_expired(current_time=current_time):
                raise AuthenticationError(
                    "Access Token 已过期",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # 会话有效性检查
            session = await uow.sessions.get_by_id(token_record.session_id)
            if session is None or session.is_expired(current_time=current_time):
                raise AuthenticationError(
                    "会话无效或已过期",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # 用户启用状态检查（SPEC §12.3：用户禁用后后续请求立即拒绝）
            user = await uow.users.get_by_id(token_record.user_id)
            if user is None or user.status is UserStatus.DISABLED:
                raise AuthenticationError(
                    "用户不可用",
                    code="AUTH.INVALID_CREDENTIALS",
                )

            # 条件更新最近活动时间（SPEC §12.3：最多每 5 分钟一次）
            await self._conditionally_touch_session(
                uow=uow,
                session=session,
                current_time=current_time,
            )

            return AuthContext(
                user_id=token_record.user_id,
                session_id=token_record.session_id,
            )

    # ------------------------------------------------------------------
    # 会话管理 Use Case（SPEC §12.3）
    # ------------------------------------------------------------------

    async def list_user_sessions(
        self,
        *,
        user_id: UUID,
    ) -> list[Session]:
        """查询用户的活动会话列表（SPEC §12.3）。"""
        async with self._uow_factory() as uow:
            return await uow.sessions.list_by_user(user_id)

    async def revoke_session(
        self,
        *,
        session_id: UUID,
        actor_id: UUID,
        current_time: datetime,
    ) -> None:
        """吊销指定会话（SPEC §12.3）。

        用户退出自己的会话（当前或其他），管理员强制下线单条会话。

        Raises:
            NotFoundError: 会话不存在
        """
        async with self._uow_factory() as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise NotFoundError(
                    "会话不存在",
                    code="AUTH.SESSION_NOT_FOUND",
                )

            if session.is_revoked:
                return  # 幂等

            reason = REASON_LOGOUT if session.user_id == actor_id else REASON_ADMIN_FORCE_LOGOUT
            await self._revoke_session_internal(
                uow=uow,
                session=session,
                reason=reason,
                current_time=current_time,
            )

    async def revoke_all_user_sessions(
        self,
        *,
        user_id: UUID,
        reason: str,
        current_time: datetime,
    ) -> int:
        """管理员强制下线——吊销用户全部活跃会话（SPEC §12.3）。

        Returns:
            被吊销的会话数量
        """
        async with self._uow_factory() as uow:
            sessions = await uow.sessions.list_by_user(user_id)
            count = 0
            for session in sessions:
                await self._revoke_session_internal(
                    uow=uow,
                    session=session,
                    reason=reason,
                    current_time=current_time,
                )
                count += 1
            return count

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    async def _check_brute_force_lock(
        self,
        uow: AuthUnitOfWork,
        normalized_username: str,
        ip: str,
        current_time: datetime,
    ) -> None:
        """检查暴力破解限制（SPEC §12.4）。

        检查账号和 IP 两个维度是否处于限制状态。任一维度触发限制时
        抛出与普通登录失败一致的 ``AuthenticationError``（SPEC §12.4：
        限制时的响应与账号密码错误响应保持一致）。

        Args:
            uow: 当前事务作用域的工作单元
            normalized_username: 规范化账号标识
            ip: 可信客户端 IP
            current_time: 当前 UTC 时间

        Raises:
            AuthenticationError: 任一维度处于限制状态
        """
        # 检查账号维度（SPEC §12.4：账号连续失败 5 次后限制 15 分钟）
        account_attempt = await uow.login_attempts.get(
            LoginAttemptDimension.ACCOUNT,
            normalized_username,
        )
        if account_attempt is not None and account_attempt.is_locked(current_time=current_time):
            raise AuthenticationError(
                "用户名或密码不正确",
                code="AUTH.INVALID_CREDENTIALS",
            )

        # 检查 IP 维度（SPEC §12.4：IP 连续失败 20 次后限制 15 分钟）
        ip_attempt = await uow.login_attempts.get(
            LoginAttemptDimension.IP,
            ip,
        )
        if ip_attempt is not None and ip_attempt.is_locked(current_time=current_time):
            raise AuthenticationError(
                "用户名或密码不正确",
                code="AUTH.INVALID_CREDENTIALS",
            )

    async def _record_brute_force_failure(
        self,
        uow: AuthUnitOfWork,
        normalized_username: str,
        ip: str,
        current_time: datetime,
    ) -> None:
        """记录暴力破解失败——递增双维度连续失败计数（SPEC §12.4）。

        在同一事务中递增账号和 IP 两个维度的失败计数。使用
        ``FOR UPDATE`` 行锁确保并发安全（SPEC §12.4：跨多 Worker 工作）。

        - 账号维度：达到 ACCOUNT_LOCK_THRESHOLD 后限制 15 分钟
        - IP 维度：达到 IP_LOCK_THRESHOLD 后限制 15 分钟
        """
        # 账号维度——递增连续失败计数
        account_attempt = await uow.login_attempts.get_for_update(
            LoginAttemptDimension.ACCOUNT,
            normalized_username,
        )
        if account_attempt is None:
            account_attempt = LoginAttempt.first_failure(
                dimension=LoginAttemptDimension.ACCOUNT,
                identifier=normalized_username,
                current_time=current_time,
            )
        else:
            account_attempt = account_attempt.increment_failure(
                threshold=ACCOUNT_LOCK_THRESHOLD,
                current_time=current_time,
            )
        await uow.login_attempts.save(account_attempt)

        # IP 维度——递增连续失败计数（成功登录不清理，SPEC §12.4）
        ip_attempt = await uow.login_attempts.get_for_update(
            LoginAttemptDimension.IP,
            ip,
        )
        if ip_attempt is None:
            ip_attempt = LoginAttempt.first_failure(
                dimension=LoginAttemptDimension.IP,
                identifier=ip,
                current_time=current_time,
            )
        else:
            ip_attempt = ip_attempt.increment_failure(
                threshold=IP_LOCK_THRESHOLD,
                current_time=current_time,
            )
        await uow.login_attempts.save(ip_attempt)

    async def _handle_replay(
        self,
        uow: AuthUnitOfWork,
        record: RefreshTokenRecord,
        current_time: datetime,
    ) -> None:
        """重放检测处理——吊销整个 Session 和 Token Family（SPEC §12.2）。

        已使用的 Refresh Token 再次出现表示 Token 泄露。安全策略要求
        立即吊销整个 Token Family 和关联会话，使攻击者获取的全部 Token 失效。
        """
        _logger.warning(
            "检测到 Refresh Token 重放——吊销 Token Family 和会话",
            extra={
                "event": "token_replay_detected",
                "token_family_id": str(record.token_family_id),
                "session_id": str(record.session_id),
                "user_id": str(record.user_id),
            },
        )

        # 吊销整个 Token Family
        await uow.refresh_tokens.revoke_by_family(
            record.token_family_id,
            REASON_REPLAY_DETECTED,
        )

        # 吊销关联会话
        session = await uow.sessions.get_by_id(record.session_id)
        if session is not None and not session.is_revoked:
            await self._revoke_session_internal(
                uow=uow,
                session=session,
                reason=REASON_REPLAY_DETECTED,
                current_time=current_time,
            )

    async def _revoke_session_internal(
        self,
        uow: AuthUnitOfWork,
        session: Session,
        reason: str,
        current_time: datetime,
    ) -> None:
        """吊销会话并清理关联 Token（内部方法）。

        在同一事务中：
        1. 更新会话状态为已吊销
        2. 删除该会话的全部 Access Token
        3. 吊销该会话的全部 Refresh Token
        4. 发布 SessionRevoked 事件
        """
        revoked_session = session.revoke(
            reason=reason,
            current_time=current_time,
        )
        await uow.sessions.update(revoked_session)

        # 删除该会话的 Access Token
        await uow.access_tokens.delete_by_session(session.id)

        # 吊销该会话的 Refresh Token
        await uow.refresh_tokens.revoke_by_session(session.id, reason)

        self._event_dispatcher.collect(
            SessionRevoked(
                occurred_at=current_time,
                session_id=session.id,
                user_id=session.user_id,
                reason=reason,
            )
        )
        await self._event_dispatcher.flush(uow)

    async def _conditionally_touch_session(
        self,
        uow: AuthUnitOfWork,
        session: Session,
        current_time: datetime,
    ) -> None:
        """条件更新最近活动时间（SPEC §12.3：最多每 5 分钟一次）。

        仅当距离上次活动时间超过 5 分钟时才写库更新，
        避免每个请求无条件写入数据库。
        """
        threshold = session.last_activity_at + timedelta(
            minutes=ACTIVITY_UPDATE_INTERVAL_MINUTES,
        )
        if current_time >= threshold:
            touched = session.touch(current_time=current_time)
            await uow.sessions.update(touched)

    def _record_login_failure(
        self,
        *,
        username: str,
        ip: str,
        user_agent: str,
        reason: str,
    ) -> None:
        """记录登录失败安全事件（SPEC §12.1、§18.1）。

        使用结构化日志记录安全事件，包含失败原因分类（SPEC §18.1：
        记录失败和失败原因分类）。不记录明文密码或 Token（SPEC §12.4）。
        审计日志持久化（G3）由审计模块独立实现。
        """
        _logger.warning(
            "登录失败",
            extra={
                "event": "login_failed",
                "username": username,
                "ip": ip,
                "user_agent": user_agent[:200],
                "reason": reason,
            },
        )

    def _record_login_success(
        self,
        *,
        username: str,
        user_id: UUID,
        session_id: UUID,
        ip: str,
        user_agent: str,
    ) -> None:
        """记录登录成功日志（SPEC §18.1：记录登录成功）。

        记录用户、会话、IP、User-Agent 信息。不记录明文密码或
        Token（SPEC §12.4）。
        """
        _logger.info(
            "登录成功",
            extra={
                "event": "login_success",
                "username": username,
                "user_id": str(user_id),
                "session_id": str(session_id),
                "ip": ip,
                "user_agent": user_agent[:200],
            },
        )
