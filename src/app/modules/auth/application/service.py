"""认证模块应用服务 / Use Case（SPEC §5.2、§5.6、§5.7、§12.1、§12.3）。

Use Case 编排密码验证、会话创建、Token 生成和事件发布：

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

安全约束：
- 登录失败不区分用户不存在与密码错误（SPEC §12.4）
- 不在日志中记录明文密码或完整 Token（SPEC §12.4）
- 固定 Argon2id 虚拟哈希校验降低响应时间差（SPEC §12.4）
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from app.errors import AuthenticationError
from app.events.dispatcher import TransactionalEventDispatcher
from app.modules.auth.application.port import (
    AuthApplicationPort,
    AuthUnitOfWork,
    LoginResult,
)
from app.modules.auth.domain.events import SessionCreated, SessionRevoked
from app.modules.auth.domain.model import (
    ACCESS_TOKEN_TTL_MINUTES,
    AccessTokenRecord,
    RefreshTokenRecord,
    Session,
)
from app.modules.auth.domain.tokens import TokenDigester, TokenGenerator
from app.modules.user.domain.model import UserStatus
from app.modules.user.domain.password import PasswordHasher

_logger = logging.getLogger("app.modules.auth.service")


class AuthService(AuthApplicationPort):
    """认证模块应用服务（SPEC §12.1、§12.3）。

    实现登录和登出 Use Case。每个写 Use Case 在独立的 Unit of Work 中执行，
    退出时统一提交或回滚。

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
        - 用户不存在时执行虚拟哈希校验（SPEC §12.4）
        - 登录前检查用户状态（SPEC §12.1）
        - 登录失败记录安全事件（SPEC §12.1）
        - ``check_needs_rehash`` 在同一事务中升级哈希（SPEC §12.1）
        - 明文 Token 绝不入库（SPEC §12.2）

        Raises:
            AuthenticationError: 用户名或密码不正确 / 用户已禁用
        """
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_username(username)

            # 用户不存在 → 虚拟哈希校验（SPEC §12.4）
            if user is None:
                self._password_hasher.verify(self._dummy_hash, password)
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
            revoked_session = session.revoke(
                reason="logout",
                current_time=current_time,
            )
            await uow.sessions.update(revoked_session)

            self._event_dispatcher.collect(
                SessionRevoked(
                    occurred_at=current_time,
                    session_id=session.id,
                    user_id=session.user_id,
                    reason="logout",
                )
            )
            await self._event_dispatcher.flush(uow)

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _record_login_failure(
        self,
        *,
        username: str,
        ip: str,
        user_agent: str,
        reason: str,
    ) -> None:
        """记录登录失败安全事件（SPEC §12.1、§18.1）。

        使用结构化日志记录安全事件。不记录明文密码（SPEC §12.4）。
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
