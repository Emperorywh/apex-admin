"""认证模块应用服务单元测试（SPEC §12.1、§12.2、§12.3、§12.4）。

使用内存假实现替代数据库，测试 AuthService 的全部 Use Case：

- 登录：成功、用户不存在（虚拟哈希）、密码错误、用户禁用、暴力破解限制
- 登出：成功、幂等（Token 不存在/会话已吊销）
- 刷新：成功、重放检测、Token 吊销/过期、会话过期/吊销、用户禁用
- 在线校验：成功、Token 无效/过期、会话无效、用户禁用
- 会话管理：列表、吊销单条、批量吊销
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest

from app.errors import AuthenticationError, NotFoundError
from app.modules.auth.application.port import (
    AuthUnitOfWork,
    LoginAttemptRepository,
)
from app.modules.auth.application.service import AuthService
from app.modules.auth.domain.login_security import (
    ACCOUNT_LOCK_THRESHOLD,
    IP_LOCK_THRESHOLD,
    LOCK_DURATION_MINUTES,
    LoginAttempt,
    LoginAttemptDimension,
)
from app.modules.auth.domain.model import (
    ACCESS_TOKEN_TTL_MINUTES,
    ACTIVITY_UPDATE_INTERVAL_MINUTES,
    AccessTokenRecord,
    RefreshTokenRecord,
    Session,
)
from app.modules.auth.domain.tokens import TokenDigester, TokenGenerator
from app.modules.user.application.port import UserRepository
from app.modules.user.domain.model import User, UserStatus
from app.modules.user.domain.password import PasswordHasher

pytestmark = [pytest.mark.unit, pytest.mark.g2]

_NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)

_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"

_PASSWORD = "TestP@ssw0rd123"


# ===========================================================================
# 内存假实现
# ===========================================================================


class FakeUserRepo(UserRepository):
    """内存用户 Repository。"""

    def __init__(self) -> None:
        self._by_id: dict[UUID, User] = {}
        self._by_username: dict[str, User] = {}

    async def add(self, entity: User) -> None:
        self._by_id[entity.id] = entity
        self._by_username[entity.username] = entity

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._by_id.get(user_id)

    async def get_by_username(self, username: str) -> User | None:
        return self._by_username.get(username)

    async def count(self) -> int:
        return len(self._by_id)

    async def list_paginated(self, offset: int, limit: int) -> list[User]:  # noqa: ARG002
        return list(self._by_id.values())

    async def update(self, entity: User) -> None:
        self._by_id[entity.id] = entity
        self._by_username[entity.username] = entity


class FakeSessionRepo:
    """内存会话 Repository。"""

    def __init__(self) -> None:
        self._by_id: dict[UUID, Session] = {}

    async def add(self, entity: Session) -> None:
        self._by_id[entity.id] = entity

    async def get_by_id(self, session_id: UUID) -> Session | None:
        return self._by_id.get(session_id)

    async def get_by_id_for_update(self, session_id: UUID) -> Session | None:
        return self._by_id.get(session_id)

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        return [s for s in self._by_id.values() if s.user_id == user_id and s.is_active]

    async def update(self, entity: Session) -> None:
        self._by_id[entity.id] = entity


class FakeAccessTokenRepo:
    """内存 Access Token Repository。"""

    def __init__(self) -> None:
        self._by_digest: dict[str, AccessTokenRecord] = {}

    async def add(self, entity: AccessTokenRecord) -> None:
        self._by_digest[entity.digest] = entity

    async def get_by_digest(self, digest: str) -> AccessTokenRecord | None:
        return self._by_digest.get(digest)

    async def delete_by_session(self, session_id: UUID) -> None:
        self._by_digest = {d: r for d, r in self._by_digest.items() if r.session_id != session_id}

    async def delete_by_user(self, user_id: UUID) -> None:
        self._by_digest = {d: r for d, r in self._by_digest.items() if r.user_id != user_id}


class FakeRefreshTokenRepo:
    """内存 Refresh Token Repository。"""

    def __init__(self) -> None:
        self._by_digest: dict[str, RefreshTokenRecord] = {}

    async def add(self, entity: RefreshTokenRecord) -> None:
        self._by_digest[entity.digest] = entity

    async def get_by_digest(self, digest: str) -> RefreshTokenRecord | None:
        return self._by_digest.get(digest)

    async def get_by_digest_for_update(self, digest: str) -> RefreshTokenRecord | None:
        return self._by_digest.get(digest)

    async def update(self, entity: RefreshTokenRecord) -> None:
        self._by_digest[entity.digest] = entity

    async def revoke_by_family(self, token_family_id: UUID, reason: str) -> int:
        count = 0
        for record in list(self._by_digest.values()):
            if record.token_family_id == token_family_id and not record.is_revoked:
                self._by_digest[record.digest] = record.revoke(reason=reason)
                count += 1
        return count

    async def revoke_by_session(self, session_id: UUID, reason: str) -> int:
        count = 0
        for record in list(self._by_digest.values()):
            if record.session_id == session_id and not record.is_revoked:
                self._by_digest[record.digest] = record.revoke(reason=reason)
                count += 1
        return count

    async def revoke_by_user(self, user_id: UUID, reason: str) -> int:
        count = 0
        for record in list(self._by_digest.values()):
            if record.user_id == user_id and not record.is_revoked:
                self._by_digest[record.digest] = record.revoke(reason=reason)
                count += 1
        return count

    async def revoke_by_user_except(self, user_id: UUID, keep_session_id: UUID, reason: str) -> int:
        count = 0
        for record in list(self._by_digest.values()):
            if (
                record.user_id == user_id
                and record.session_id != keep_session_id
                and not record.is_revoked
            ):
                self._by_digest[record.digest] = record.revoke(reason=reason)
                count += 1
        return count


class FakeLoginAttemptRepo(LoginAttemptRepository):
    """内存登录失败记录 Repository。"""

    def __init__(self) -> None:
        self._data: dict[tuple[LoginAttemptDimension, str], LoginAttempt] = {}

    async def get(self, dimension: LoginAttemptDimension, identifier: str) -> LoginAttempt | None:
        return self._data.get((dimension, identifier))

    async def get_for_update(
        self, dimension: LoginAttemptDimension, identifier: str
    ) -> LoginAttempt | None:
        return self._data.get((dimension, identifier))

    async def save(self, entity: LoginAttempt) -> None:
        self._data[(entity.dimension, entity.identifier)] = entity

    async def delete(self, dimension: LoginAttemptDimension, identifier: str) -> None:
        self._data.pop((dimension, identifier), None)


class FakeAuthUow(AuthUnitOfWork):
    """内存认证工作单元。"""

    def __init__(self) -> None:
        self._users = FakeUserRepo()
        self._sessions = FakeSessionRepo()
        self._access_tokens = FakeAccessTokenRepo()
        self._refresh_tokens = FakeRefreshTokenRepo()
        self._login_attempts = FakeLoginAttemptRepo()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    @property
    def users(self) -> FakeUserRepo:
        return self._users

    @property
    def sessions(self) -> FakeSessionRepo:
        return self._sessions

    @property
    def access_tokens(self) -> FakeAccessTokenRepo:
        return self._access_tokens

    @property
    def refresh_tokens(self) -> FakeRefreshTokenRepo:
        return self._refresh_tokens

    @property
    def login_attempts(self) -> FakeLoginAttemptRepo:
        return self._login_attempts


# ===========================================================================
# 辅助工厂
# ===========================================================================


class _NoOpEventDispatcher:
    """测试用空事件调度器——收集但不执行任何处理器。"""

    def collect(self, event: object) -> None:  # noqa: ARG002
        pass

    async def flush(self, uow: object) -> None:  # noqa: ARG002
        pass


def _make_password_hasher() -> PasswordHasher:
    return PasswordHasher()


def _make_token_digester() -> TokenDigester:
    from pydantic import SecretStr

    return TokenDigester(
        access_key=SecretStr(_ACCESS_KEY),
        refresh_key=SecretStr(_REFRESH_KEY),
    )


def _make_auth_service(uow: FakeAuthUow) -> AuthService:
    """构造使用内存假 UoW 的 AuthService。"""
    hasher = _make_password_hasher()
    digester = _make_token_digester()
    generator = TokenGenerator()
    dispatcher = _NoOpEventDispatcher()

    def uow_factory() -> FakeAuthUow:
        return uow

    return AuthService(
        uow_factory=uow_factory,
        password_hasher=hasher,
        token_generator=generator,
        token_digester=digester,
        event_dispatcher=dispatcher,  # type: ignore[arg-type]
    )


def _make_user(
    *,
    username: str = "testuser",
    password: str = _PASSWORD,
    status: UserStatus = UserStatus.ACTIVE,
    current_time: datetime = _NOW,
) -> User:
    from dataclasses import replace as dc_replace

    hasher = _make_password_hasher()
    user = User.new(
        username=username,
        display_name=username,
        password_hash=hasher.hash(password),
        current_time=current_time,
    )
    if status is not UserStatus.ACTIVE:
        user = dc_replace(user, status=status)
    return user


async def _do_login(
    uow: FakeAuthUow,
    service: AuthService,
    *,
    username: str = "testuser",
    password: str = _PASSWORD,
    ip: str = "127.0.0.1",
    user_agent: str = "TestAgent/1.0",
):
    """执行登录并返回结果。"""
    return await service.login(
        username=username,
        password=password,
        ip=ip,
        user_agent=user_agent,
        device=None,
        current_time=_NOW,
    )


async def _setup_logged_in(
    *,
    username: str = "testuser",
    password: str = _PASSWORD,
    status: UserStatus = UserStatus.ACTIVE,
    ip: str = "127.0.0.1",
) -> tuple[FakeAuthUow, AuthService, object]:
    """设置用户、登录，返回 (uow, service, login_result)。"""
    uow = FakeAuthUow()
    user = _make_user(username=username, password=password, status=status)
    await uow.users.add(user)
    service = _make_auth_service(uow)
    result = await _do_login(uow, service, username=username, password=password, ip=ip)
    return uow, service, result


# ===========================================================================
# 登录 Use Case
# ===========================================================================


class TestLogin:
    """登录 Use Case 测试（SPEC §12.1、§12.4）。"""

    async def test_login_success(self) -> None:
        """有效凭据登录成功，返回 Token。"""
        uow = FakeAuthUow()
        user = _make_user()
        await uow.users.add(user)
        service = _make_auth_service(uow)

        result = await _do_login(uow, service)

        assert result.access_token
        assert result.refresh_token
        assert result.user_id == user.id
        assert result.access_token_expires_in == ACCESS_TOKEN_TTL_MINUTES * 60

    async def test_login_user_not_found(self) -> None:
        """用户不存在返回认证错误（SPEC §12.4：不区分原因）。"""
        uow = FakeAuthUow()
        service = _make_auth_service(uow)

        with pytest.raises(AuthenticationError, match="不正确"):
            await _do_login(uow, service, username="nobody")

    async def test_login_wrong_password(self) -> None:
        """密码错误返回认证错误。"""
        uow = FakeAuthUow()
        user = _make_user()
        await uow.users.add(user)
        service = _make_auth_service(uow)

        with pytest.raises(AuthenticationError, match="不正确"):
            await _do_login(uow, service, password="WrongPassword123!")

    async def test_login_user_disabled(self) -> None:
        """禁用用户登录返回认证错误（SPEC §12.1）。"""
        uow = FakeAuthUow()
        user = _make_user(status=UserStatus.DISABLED)
        await uow.users.add(user)
        service = _make_auth_service(uow)

        with pytest.raises(AuthenticationError, match="不正确"):
            await _do_login(uow, service)

    async def test_login_brute_force_account_lock(self) -> None:
        """账号维度暴力破解限制（SPEC §12.4）。"""
        uow = FakeAuthUow()
        user = _make_user()
        await uow.users.add(user)
        service = _make_auth_service(uow)

        locked = LoginAttempt(
            dimension=LoginAttemptDimension.ACCOUNT,
            identifier="testuser",
            failure_count=ACCOUNT_LOCK_THRESHOLD,
            locked_until=_NOW + timedelta(minutes=LOCK_DURATION_MINUTES),
            last_failure_at=_NOW,
        )
        await uow.login_attempts.save(locked)

        with pytest.raises(AuthenticationError, match="不正确"):
            await _do_login(uow, service)

    async def test_login_brute_force_ip_lock(self) -> None:
        """IP 维度暴力破解限制（SPEC §12.4）。"""
        uow = FakeAuthUow()
        user = _make_user()
        await uow.users.add(user)
        service = _make_auth_service(uow)

        locked = LoginAttempt(
            dimension=LoginAttemptDimension.IP,
            identifier="192.168.1.1",
            failure_count=IP_LOCK_THRESHOLD,
            locked_until=_NOW + timedelta(minutes=LOCK_DURATION_MINUTES),
            last_failure_at=_NOW,
        )
        await uow.login_attempts.save(locked)

        with pytest.raises(AuthenticationError, match="不正确"):
            await _do_login(uow, service, ip="192.168.1.1")

    async def test_login_success_clears_account_failures(self) -> None:
        """成功登录清理账号维度失败状态（SPEC §12.4）。"""
        uow = FakeAuthUow()
        user = _make_user()
        await uow.users.add(user)
        service = _make_auth_service(uow)

        await uow.login_attempts.save(
            LoginAttempt.first_failure(
                dimension=LoginAttemptDimension.ACCOUNT,
                identifier="testuser",
                current_time=_NOW,
            )
        )

        await _do_login(uow, service)

        record = await uow.login_attempts.get(LoginAttemptDimension.ACCOUNT, "testuser")
        assert record is None

    async def test_login_failure_increments_brute_force(self) -> None:
        """登录失败递增双维度计数（SPEC §12.4）。"""
        uow = FakeAuthUow()
        service = _make_auth_service(uow)

        with pytest.raises(AuthenticationError):
            await _do_login(uow, service, username="nobody", ip="10.0.0.1")

        acct = await uow.login_attempts.get(LoginAttemptDimension.ACCOUNT, "nobody")
        ip = await uow.login_attempts.get(LoginAttemptDimension.IP, "10.0.0.1")
        assert acct is not None and acct.failure_count == 1
        assert ip is not None and ip.failure_count == 1


# ===========================================================================
# 登出 Use Case
# ===========================================================================


class TestLogout:
    """登出 Use Case 测试（SPEC §12.3）。"""

    async def test_logout_success(self) -> None:
        """有效 Refresh Token 登出成功，吊销会话。"""
        uow, service, result = await _setup_logged_in()

        await service.logout(
            refresh_token=result.refresh_token,
            current_time=_NOW + timedelta(minutes=1),
        )

        session = await uow.sessions.get_by_id(result.session_id)
        assert session is not None
        assert session.is_revoked

    async def test_logout_invalid_token_idempotent(self) -> None:
        """无效 Token 登出幂等成功（不报错）。"""
        uow = FakeAuthUow()
        service = _make_auth_service(uow)

        await service.logout(refresh_token="invalid", current_time=_NOW)

    async def test_logout_already_revoked_idempotent(self) -> None:
        """已吊销会话的登出幂等成功。"""
        uow, service, result = await _setup_logged_in()

        await service.logout(refresh_token=result.refresh_token, current_time=_NOW)
        # 第二次——幂等
        await service.logout(
            refresh_token=result.refresh_token,
            current_time=_NOW + timedelta(minutes=1),
        )


# ===========================================================================
# 刷新 Use Case
# ===========================================================================


class TestRefresh:
    """刷新 Use Case 测试（SPEC §12.2）。"""

    async def test_refresh_success(self) -> None:
        """有效 Refresh Token 刷新成功，返回新 Token。"""
        uow, service, login_result = await _setup_logged_in()

        result = await service.refresh(
            refresh_token=login_result.refresh_token,
            current_time=_NOW + timedelta(minutes=5),
        )

        assert result.access_token != login_result.access_token
        assert result.refresh_token != login_result.refresh_token
        assert result.session_id == login_result.session_id

    async def test_refresh_invalid_token(self) -> None:
        """无效 Token 刷新返回认证错误。"""
        uow, _, _ = await _setup_logged_in()
        service = _make_auth_service(uow)

        with pytest.raises(AuthenticationError, match="无效"):
            await service.refresh(refresh_token="invalid", current_time=_NOW)

    async def test_refresh_replay_detection(self) -> None:
        """已使用的 Token 再次刷新触发重放检测（SPEC §12.2）。"""
        uow, service, login_result = await _setup_logged_in()

        # 第一次刷新成功
        await service.refresh(
            refresh_token=login_result.refresh_token,
            current_time=_NOW + timedelta(minutes=5),
        )

        # 用旧 Token 再次刷新——重放检测
        with pytest.raises(AuthenticationError, match="重放"):
            await service.refresh(
                refresh_token=login_result.refresh_token,
                current_time=_NOW + timedelta(minutes=6),
            )

        session = await uow.sessions.get_by_id(login_result.session_id)
        assert session is not None
        assert session.is_revoked

    async def test_refresh_revoked_token(self) -> None:
        """已吊销的 Token 刷新返回认证错误。"""
        _, service, login_result = await _setup_logged_in()

        await service.logout(refresh_token=login_result.refresh_token, current_time=_NOW)

        with pytest.raises(AuthenticationError, match="已吊销"):
            await service.refresh(
                refresh_token=login_result.refresh_token,
                current_time=_NOW + timedelta(minutes=1),
            )

    async def test_refresh_expired_token(self) -> None:
        """已过期的 Token 刷新返回认证错误。"""
        _, service, login_result = await _setup_logged_in()

        with pytest.raises(AuthenticationError):
            await service.refresh(
                refresh_token=login_result.refresh_token,
                current_time=_NOW + timedelta(hours=13),
            )

    async def test_refresh_user_disabled(self) -> None:
        """用户被禁用后刷新返回认证错误（SPEC §12.2）。"""
        from dataclasses import replace as dc_replace

        uow, service, login_result = await _setup_logged_in()

        user = await uow.users.get_by_username("testuser")
        assert user is not None
        await uow.users.update(dc_replace(user, status=UserStatus.DISABLED))

        with pytest.raises(AuthenticationError, match="用户不可用"):
            await service.refresh(
                refresh_token=login_result.refresh_token,
                current_time=_NOW + timedelta(minutes=5),
            )


# ===========================================================================
# 在线校验 Use Case
# ===========================================================================


class TestValidateAccessToken:
    """在线校验 Use Case 测试（SPEC §12.3）。"""

    async def test_validate_success(self) -> None:
        """有效 Access Token 校验成功。"""
        _, service, login_result = await _setup_logged_in()

        ctx = await service.validate_access_token(
            access_token=login_result.access_token,
            current_time=_NOW + timedelta(minutes=1),
        )

        assert ctx.user_id == login_result.user_id
        assert ctx.session_id == login_result.session_id

    async def test_validate_invalid_token(self) -> None:
        """无效 Token 返回认证错误。"""
        uow = FakeAuthUow()
        service = _make_auth_service(uow)

        with pytest.raises(AuthenticationError, match="无效"):
            await service.validate_access_token(access_token="invalid", current_time=_NOW)

    async def test_validate_expired_token(self) -> None:
        """过期 Token 返回认证错误。"""
        _, service, login_result = await _setup_logged_in()

        with pytest.raises(AuthenticationError, match="已过期"):
            await service.validate_access_token(
                access_token=login_result.access_token,
                current_time=_NOW + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES + 1),
            )

    async def test_validate_revoked_session(self) -> None:
        """吊销会话后 Token 校验返回认证错误（AT 被删除）。"""
        uow, service, login_result = await _setup_logged_in()

        # 吊销会话（同时删除 Access Token）
        await service.revoke_session(
            session_id=login_result.session_id,
            actor_id=login_result.user_id,
            current_time=_NOW,
        )

        with pytest.raises(AuthenticationError, match="无效"):
            await service.validate_access_token(
                access_token=login_result.access_token,
                current_time=_NOW + timedelta(minutes=1),
            )

    async def test_validate_user_disabled(self) -> None:
        """用户禁用后 Token 立即失效（SPEC §12.3）。"""
        from dataclasses import replace as dc_replace

        uow, service, login_result = await _setup_logged_in()

        user = await uow.users.get_by_username("testuser")
        assert user is not None
        await uow.users.update(dc_replace(user, status=UserStatus.DISABLED))

        with pytest.raises(AuthenticationError, match="用户不可用"):
            await service.validate_access_token(
                access_token=login_result.access_token,
                current_time=_NOW + timedelta(minutes=1),
            )

    async def test_validate_touches_session_after_interval(self) -> None:
        """超过 5 分钟后校验触发会话活动时间更新（SPEC §12.3）。"""
        uow, service, login_result = await _setup_logged_in()

        session_before = await uow.sessions.get_by_id(login_result.session_id)
        assert session_before is not None

        new_time = _NOW + timedelta(minutes=ACTIVITY_UPDATE_INTERVAL_MINUTES + 1)
        await service.validate_access_token(
            access_token=login_result.access_token,
            current_time=new_time,
        )

        session_after = await uow.sessions.get_by_id(login_result.session_id)
        assert session_after is not None
        assert session_after.last_activity_at > session_before.last_activity_at


# ===========================================================================
# 会话管理 Use Case
# ===========================================================================


class TestSessionManagement:
    """会话管理 Use Case 测试（SPEC §12.3）。"""

    async def test_list_user_sessions(self) -> None:
        """查询用户活动会话列表。"""
        uow, service, login_result = await _setup_logged_in()

        sessions = await service.list_user_sessions(user_id=login_result.user_id)
        assert len(sessions) == 1
        assert sessions[0].id == login_result.session_id

    async def test_revoke_session_not_found(self) -> None:
        """吊销不存在的会话返回 NotFoundError。"""
        uow = FakeAuthUow()
        service = _make_auth_service(uow)

        with pytest.raises(NotFoundError, match="不存在"):
            await service.revoke_session(
                session_id=uuid4(),
                actor_id=uuid4(),
                current_time=_NOW,
            )

    async def test_revoke_session_by_owner(self) -> None:
        """用户吊销自己的会话。"""
        _, service, login_result = await _setup_logged_in()

        await service.revoke_session(
            session_id=login_result.session_id,
            actor_id=login_result.user_id,
            current_time=_NOW + timedelta(minutes=1),
        )

    async def test_revoke_session_already_revoked_idempotent(self) -> None:
        """已吊销会话再次吊销幂等成功。"""
        _, service, login_result = await _setup_logged_in()

        await service.revoke_session(
            session_id=login_result.session_id,
            actor_id=login_result.user_id,
            current_time=_NOW,
        )
        # 第二次——幂等
        await service.revoke_session(
            session_id=login_result.session_id,
            actor_id=login_result.user_id,
            current_time=_NOW + timedelta(minutes=1),
        )

    async def test_revoke_all_user_sessions(self) -> None:
        """管理员批量吊销用户全部活跃会话。"""
        uow = FakeAuthUow()
        user = _make_user()
        await uow.users.add(user)
        service = _make_auth_service(uow)

        r1 = await _do_login(uow, service, user_agent="A")
        await _do_login(uow, service, user_agent="B")

        count = await service.revoke_all_user_sessions(
            user_id=r1.user_id,
            reason="admin_force_logout",
            current_time=_NOW + timedelta(minutes=1),
        )

        assert count == 2
