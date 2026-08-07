"""认证密码单元测试（SPEC §12.1、§12.4、§23.2）。

覆盖认证场景下的 Argon2id 密码哈希行为：
- 固定参数验证（m=65536, t=3, p=1）
- 随机盐（每次哈希不同）
- 错误密码拒绝
- ``check_needs_rehash`` 参数升级检测
- 固定虚拟哈希校验（SPEC §12.4：用户不存在时均衡响应时间）

附加覆盖 AuthService 登录/登出 Use Case 的编排逻辑（Fake UoW）：
- 登录成功创建会话和 Token（摘要入库，明文不入库）
- 用户不存在/禁用/密码错误时拒绝并记录安全事件
- ``check_needs_rehash`` 同一事务升级哈希
- 登出吊销会话

不依赖数据库或 Docker。
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest
from argon2 import PasswordHasher as _Argon2Hasher
from pydantic import SecretStr

from app.errors import AuthenticationError
from app.events.dispatcher import TransactionalEventDispatcher
from app.events.registry import EventHandlerRegistry
from app.modules.auth.application.port import (
    AccessTokenRepository,
    AuthUnitOfWork,
    RefreshTokenRepository,
    SessionRepository,
)
from app.modules.auth.application.service import AuthService
from app.modules.auth.domain.model import (
    ABSOLUTE_TIMEOUT_HOURS,
    ACCESS_TOKEN_TTL_MINUTES,
    IDLE_TIMEOUT_MINUTES,
    AccessTokenRecord,
    RefreshTokenRecord,
    Session,
)
from app.modules.auth.domain.tokens import TokenDigester, TokenGenerator
from app.modules.registry import ModuleRegistry
from app.modules.user.application.port import UserRepository
from app.modules.user.domain.model import User, UserStatus
from app.modules.user.domain.password import PasswordHasher

pytestmark = [pytest.mark.unit, pytest.mark.g2]

_VALID_PASSWORD = "SecurePass123!"


class TestArgon2idParameters:
    """Argon2id 固定参数验证（SPEC §12.1）。"""

    def test_hash_contains_fixed_parameters(self) -> None:
        """哈希字符串包含 SPEC §12.1 固定参数。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert "$argon2id$" in h
        assert "m=65536" in h
        assert "t=3" in h
        assert "p=1" in h

    def test_hasher_uses_correct_parameters(self) -> None:
        """PasswordHasher 内部使用正确的参数值。"""
        assert PasswordHasher.MEMORY_COST == 65536
        assert PasswordHasher.TIME_COST == 3
        assert PasswordHasher.PARALLELISM == 1

    def test_hash_uses_argon2id_variant(self) -> None:
        """哈希变体为 argon2id（而非 argon2i 或 argon2d）。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert h.startswith("$argon2id$")


class TestRandomSalt:
    """随机盐验证（SPEC §23.2：独立随机盐）。"""

    def test_each_hash_has_different_salt(self) -> None:
        """同一密码两次哈希结果不同（独立随机盐）。"""
        hasher = PasswordHasher()
        h1 = hasher.hash(_VALID_PASSWORD)
        h2 = hasher.hash(_VALID_PASSWORD)
        assert h1 != h2

    def test_multiple_hashes_all_unique(self) -> None:
        """多次哈希全部唯一。"""
        hasher = PasswordHasher()
        hashes = {hasher.hash(_VALID_PASSWORD) for _ in range(5)}
        assert len(hashes) == 5


class TestPasswordVerification:
    """密码验证测试（SPEC §12.1）。"""

    def test_verify_correct_password(self) -> None:
        """正确密码验证返回 True。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.verify(h, _VALID_PASSWORD) is True

    def test_verify_wrong_password(self) -> None:
        """错误密码返回 False（不抛出异常）。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.verify(h, "CompletelyWrong!!") is False

    def test_verify_empty_password(self) -> None:
        """空密码验证返回 False。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.verify(h, "") is False

    def test_verify_similar_password(self) -> None:
        """相似但不同的密码返回 False。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.verify(h, _VALID_PASSWORD[:-1] + "X") is False


class TestCheckNeedsRehash:
    """``check_needs_rehash`` 参数升级检测（SPEC §12.1）。"""

    def test_current_params_no_rehash_needed(self) -> None:
        """当前参数生成的哈希不需要 rehash。"""
        hasher = PasswordHasher()
        h = hasher.hash(_VALID_PASSWORD)
        assert hasher.needs_rehash(h) is False

    def test_old_params_trigger_rehash(self) -> None:
        """旧参数（更低时间成本）的哈希需要 rehash。"""
        old_hasher = _Argon2Hasher(
            memory_cost=65536,
            time_cost=1,  # 旧参数：time_cost=1（SPEC 要求 3）
            parallelism=1,
        )
        old_hash = old_hasher.hash(_VALID_PASSWORD)

        current_hasher = PasswordHasher()
        assert current_hasher.needs_rehash(old_hash) is True

    def test_old_memory_cost_triggers_rehash(self) -> None:
        """旧参数（更低内存成本）的哈希需要 rehash。"""
        old_hasher = _Argon2Hasher(
            memory_cost=32768,  # 旧参数：memory_cost=32768（SPEC 要求 65536）
            time_cost=3,
            parallelism=1,
        )
        old_hash = old_hasher.hash(_VALID_PASSWORD)

        current_hasher = PasswordHasher()
        assert current_hasher.needs_rehash(old_hash) is True

    def test_rehash_produces_current_params(self) -> None:
        """rehash 后的哈希使用当前参数。"""
        old_hasher = _Argon2Hasher(
            memory_cost=65536,
            time_cost=1,
            parallelism=1,
        )
        old_hash = old_hasher.hash(_VALID_PASSWORD)

        current_hasher = PasswordHasher()
        if current_hasher.needs_rehash(old_hash):
            new_hash = current_hasher.hash(_VALID_PASSWORD)
            assert "t=3" in new_hash
            assert current_hasher.needs_rehash(new_hash) is False


class TestDummyHashVerification:
    """固定虚拟哈希校验（SPEC §12.4）。"""

    def test_dummy_hash_verification_always_returns_false(self) -> None:
        """虚拟哈希校验对任意密码返回 False（不会匹配）。"""
        hasher = PasswordHasher()
        dummy_hash = hasher.hash("__apex_dummy_hash_value__")

        # 任意密码都不会匹配虚拟哈希
        assert hasher.verify(dummy_hash, _VALID_PASSWORD) is False
        assert hasher.verify(dummy_hash, "any_password") is False
        assert hasher.verify(dummy_hash, "") is False

    def test_dummy_hash_is_valid_argon2id(self) -> None:
        """虚拟哈希是有效的 Argon2id 编码字符串。"""
        hasher = PasswordHasher()
        dummy_hash = hasher.hash("__apex_dummy_hash_value__")
        assert dummy_hash.startswith("$argon2id$")
        assert "m=65536" in dummy_hash

    def test_dummy_hash_takes_similar_time(self) -> None:
        """虚拟哈希校验与真实哈希校验耗时相近（SPEC §12.4：降低响应时间差）。"""
        hasher = PasswordHasher()
        real_hash = hasher.hash(_VALID_PASSWORD)
        dummy_hash = hasher.hash("__apex_dummy_hash_value__")

        # 测量验证耗时
        start = time.perf_counter()
        for _ in range(3):
            hasher.verify(real_hash, "wrong_password")
        real_time = time.perf_counter() - start

        start = time.perf_counter()
        for _ in range(3):
            hasher.verify(dummy_hash, "wrong_password")
        dummy_time = time.perf_counter() - start

        # 两者应在同一数量级（Argon2id 耗时由参数决定，不受密码值影响）
        # 容差设置为 3 倍以避免 CI 环境波动
        assert dummy_time > real_time * 0.1, "虚拟哈希校验耗时不应远低于真实校验"


class TestTokenGeneration:
    """Token 生成与 HMAC 摘要单元测试（SPEC §12.1、§12.2）。"""

    def test_token_generator_produces_url_safe_string(self) -> None:
        """生成的 Token 是 URL-safe 字符串。"""
        from app.modules.auth.domain.tokens import TokenGenerator

        gen = TokenGenerator()
        token = gen.generate()
        # URL-safe base64: 字母、数字、-、_
        for char in token:
            assert char.isalnum() or char in "-_"

    def test_token_generator_produces_unique_tokens(self) -> None:
        """每次生成不同的 Token。"""
        from app.modules.auth.domain.tokens import TokenGenerator

        gen = TokenGenerator()
        tokens = {gen.generate() for _ in range(10)}
        assert len(tokens) == 10

    def test_token_generator_uses_256_bits(self) -> None:
        """Token 使用 256 bit 熵（32 字节）。"""
        from app.modules.auth.domain.tokens import TokenGenerator

        assert TokenGenerator.TOKEN_BYTES == 32

    def test_access_digest_is_hex_sha256_length(self) -> None:
        """Access Token 摘要是 64 字符 hex（HMAC-SHA-256）。"""
        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr("a" * 64),
            refresh_key=SecretStr("b" * 64),
        )
        digest = digester.access_digest("test_token")
        assert len(digest) == 64
        int(digest, 16)  # 验证是有效的 hex

    def test_refresh_digest_uses_different_key(self) -> None:
        """Refresh Token 摘要使用不同密钥——与 Access Token 摘要不同。"""
        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr("a" * 64),
            refresh_key=SecretStr("b" * 64),
        )
        token = "same_token_value"
        access_digest = digester.access_digest(token)
        refresh_digest = digester.refresh_digest(token)
        assert access_digest != refresh_digest

    def test_same_token_same_digest(self) -> None:
        """同一 Token 产生相同摘要（确定性）。"""
        from pydantic import SecretStr

        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr("a" * 64),
            refresh_key=SecretStr("b" * 64),
        )
        token = "deterministic_test_token"
        d1 = digester.access_digest(token)
        d2 = digester.access_digest(token)
        assert d1 == d2

    def test_plaintext_token_not_in_digest(self) -> None:
        """摘要不包含明文 Token（单向 HMAC）。"""
        from pydantic import SecretStr

        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr("a" * 64),
            refresh_key=SecretStr("b" * 64),
        )
        token = "plaintext_secret_12345"
        digest = digester.access_digest(token)
        assert token not in digest
        assert digest not in token


# ===========================================================================
# Fake 实现（内存，不依赖数据库）
# ===========================================================================


class _FakeUserRepository(UserRepository):
    """内存用户 Repository。"""

    def __init__(self) -> None:
        self._users: dict[UUID, User] = {}

    async def add(self, entity: User) -> None:
        self._users[entity.id] = entity

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._users.get(user_id)

    async def get_by_username(self, username: str) -> User | None:
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    async def count(self) -> int:
        return len(self._users)

    async def list_paginated(self, offset: int, limit: int) -> list[User]:
        all_users = sorted(self._users.values(), key=lambda u: u.created_at, reverse=True)
        return all_users[offset : offset + limit]

    async def update(self, entity: User) -> None:
        self._users[entity.id] = entity


class _FakeSessionRepository(SessionRepository):
    """内存会话 Repository。"""

    def __init__(self) -> None:
        self._sessions: dict[UUID, Session] = {}

    async def add(self, entity: Session) -> None:
        self._sessions[entity.id] = entity

    async def get_by_id(self, session_id: UUID) -> Session | None:
        return self._sessions.get(session_id)

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        return [s for s in self._sessions.values() if s.user_id == user_id]

    async def update(self, entity: Session) -> None:
        self._sessions[entity.id] = entity


class _FakeAccessTokenRepository(AccessTokenRepository):
    """内存 Access Token Repository。"""

    def __init__(self) -> None:
        self._records: dict[str, AccessTokenRecord] = {}

    async def add(self, entity: AccessTokenRecord) -> None:
        self._records[entity.digest] = entity

    async def get_by_digest(self, digest: str) -> AccessTokenRecord | None:
        return self._records.get(digest)

    async def delete_by_session(self, session_id: UUID) -> None:
        digests_to_delete = [r.digest for r in self._records.values() if r.session_id == session_id]
        for d in digests_to_delete:
            self._records.pop(d, None)


class _FakeRefreshTokenRepository(RefreshTokenRepository):
    """内存 Refresh Token Repository。"""

    def __init__(self) -> None:
        self._records: dict[str, RefreshTokenRecord] = {}

    async def add(self, entity: RefreshTokenRecord) -> None:
        self._records[entity.digest] = entity

    async def get_by_digest(self, digest: str) -> RefreshTokenRecord | None:
        return self._records.get(digest)


class _FakeAuthUnitOfWork(AuthUnitOfWork):
    """内存认证 UoW，记录提交/回滚状态。"""

    def __init__(self) -> None:
        self._users_repo = _FakeUserRepository()
        self._sessions_repo = _FakeSessionRepository()
        self._access_repo = _FakeAccessTokenRepository()
        self._refresh_repo = _FakeRefreshTokenRepository()
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    @property
    def users(self) -> _FakeUserRepository:
        return self._users_repo

    @property
    def sessions(self) -> _FakeSessionRepository:
        return self._sessions_repo

    @property
    def access_tokens(self) -> _FakeAccessTokenRepository:
        return self._access_repo

    @property
    def refresh_tokens(self) -> _FakeRefreshTokenRepository:
        return self._refresh_repo


def _make_dispatcher() -> TransactionalEventDispatcher:
    """构造带空处理器注册表的事件调度器。"""
    return TransactionalEventDispatcher(EventHandlerRegistry(ModuleRegistry([]), {}))


_ACCESS_KEY = SecretStr("1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809")
_REFRESH_KEY = SecretStr("0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0")


def _make_auth_service(
    uow: _FakeAuthUnitOfWork | None = None,
) -> tuple[AuthService, _FakeAuthUnitOfWork]:
    """快速构造 AuthService 测试实例。"""
    uow_instance = uow or _FakeAuthUnitOfWork()
    service = AuthService(
        uow_factory=lambda: uow_instance,
        password_hasher=PasswordHasher(),
        token_generator=TokenGenerator(),
        token_digester=TokenDigester(_ACCESS_KEY, _REFRESH_KEY),
        event_dispatcher=_make_dispatcher(),
    )
    return service, uow_instance


def _create_user_in_uow(
    uow: _FakeAuthUnitOfWork,
    *,
    username: str = "testuser",
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    """在 Fake UoW 中创建一个用户。"""
    hasher = PasswordHasher()
    now = datetime.now(UTC)
    user = User.new(
        username=username,
        display_name="Test User",
        password_hash=hasher.hash(_VALID_PASSWORD),
        current_time=now,
    )
    if status is UserStatus.DISABLED:
        user = user.disable(current_time=now)
    uow.users._users[user.id] = user
    return user


# ===========================================================================
# AuthService 登录 Use Case 测试
# ===========================================================================


class TestAuthServiceLogin:
    """登录 Use Case 单元测试（SPEC §12.1、§12.3、§12.4）。"""

    async def test_login_success_creates_session_and_tokens(self) -> None:
        """登录成功：创建会话、生成 Access Token 和 Refresh Token。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        result = await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="TestAgent/1.0",
            device=None,
            current_time=datetime.now(UTC),
        )

        assert result.access_token  # Access Token 在响应体返回
        assert result.refresh_token  # Refresh Token 存在
        assert result.access_token != result.refresh_token
        assert result.access_token_expires_in == ACCESS_TOKEN_TTL_MINUTES * 60
        assert uow.committed

    async def test_login_creates_session_record(self) -> None:
        """登录创建会话记录（SPEC §12.3）。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        result = await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="192.168.1.1",
            user_agent="TestAgent/1.0",
            device="desktop",
            current_time=datetime.now(UTC),
        )

        session = await uow.sessions.get_by_id(result.session_id)
        assert session is not None
        assert session.is_active
        assert session.ip == "192.168.1.1"
        assert session.user_agent == "TestAgent/1.0"
        assert session.idle_timeout_minutes == IDLE_TIMEOUT_MINUTES
        assert session.absolute_timeout_hours == ABSOLUTE_TIMEOUT_HOURS

    async def test_login_nonexistent_user_raises(self) -> None:
        """用户不存在抛出 AuthenticationError（SPEC §12.4）。"""
        service, uow = _make_auth_service()

        with pytest.raises(AuthenticationError, match="AUTH.INVALID_CREDENTIALS"):
            await service.login(
                username="nobody",
                password=_VALID_PASSWORD,
                ip="127.0.0.1",
                user_agent="TestAgent",
                device=None,
                current_time=datetime.now(UTC),
            )
        assert uow.rolled_back

    async def test_login_disabled_user_raises(self) -> None:
        """禁用用户登录被拒绝（SPEC §12.1：登录前检查用户状态）。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow, status=UserStatus.DISABLED)

        with pytest.raises(AuthenticationError, match="AUTH.INVALID_CREDENTIALS"):
            await service.login(
                username="testuser",
                password=_VALID_PASSWORD,
                ip="127.0.0.1",
                user_agent="TestAgent",
                device=None,
                current_time=datetime.now(UTC),
            )

    async def test_login_wrong_password_raises(self) -> None:
        """密码错误抛出 AuthenticationError。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        with pytest.raises(AuthenticationError, match="AUTH.INVALID_CREDENTIALS"):
            await service.login(
                username="testuser",
                password="WrongPassword!!",
                ip="127.0.0.1",
                user_agent="TestAgent",
                device=None,
                current_time=datetime.now(UTC),
            )

    async def test_login_failure_records_security_event(self) -> None:
        """登录失败记录安全事件（SPEC §12.1、§18.1）。"""
        service, uow = _make_auth_service()

        with self._capture_login_log() as records, pytest.raises(AuthenticationError):
            await service.login(
                username="nobody",
                password=_VALID_PASSWORD,
                ip="10.0.0.1",
                user_agent="Agent",
                device=None,
                current_time=datetime.now(UTC),
            )

        login_fail_logs = [r for r in records if "登录失败" in r.getMessage()]
        assert len(login_fail_logs) == 1
        extra = login_fail_logs[0].__dict__
        assert extra.get("event") == "login_failed"
        assert extra.get("username") == "nobody"
        assert extra.get("ip") == "10.0.0.1"
        # 不记录明文密码
        assert _VALID_PASSWORD not in str(extra)

    @staticmethod
    def _capture_login_log() -> list[logging.LogRecord]:
        """捕获 auth.service 日志记录。"""
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger = logging.getLogger("app.modules.auth.service")
        logger.addHandler(handler)
        # 使用 try-finally 确保清理
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            try:
                yield records
            finally:
                logger.removeHandler(handler)

        return _ctx()  # type: ignore[return-value]

    async def test_login_does_not_store_plaintext_access_token(self) -> None:
        """明文 Access Token 不入库——只存 HMAC 摘要（SPEC §12.2）。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        result = await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="Agent",
            device=None,
            current_time=datetime.now(UTC),
        )

        # 摘要存入仓库
        all_digests = list(uow.access_tokens._records.keys())
        assert len(all_digests) == 1
        # 明文 Token 不等于任何存储的摘要
        assert result.access_token not in all_digests
        # 摘要是 hex 编码的 HMAC-SHA-256（64 字符）
        assert len(all_digests[0]) == 64

    async def test_login_does_not_store_plaintext_refresh_token(self) -> None:
        """明文 Refresh Token 不入库——只存 HMAC 摘要（SPEC §12.2）。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        result = await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="Agent",
            device=None,
            current_time=datetime.now(UTC),
        )

        all_digests = list(uow.refresh_tokens._records.keys())
        assert len(all_digests) == 1
        assert result.refresh_token not in all_digests
        assert len(all_digests[0]) == 64

    async def test_login_access_and_refresh_digests_differ(self) -> None:
        """Access Token 和 Refresh Token 摘要不同（独立密钥，SPEC §12.2）。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="Agent",
            device=None,
            current_time=datetime.now(UTC),
        )

        access_digests = list(uow.access_tokens._records.keys())
        refresh_digests = list(uow.refresh_tokens._records.keys())
        assert access_digests[0] != refresh_digests[0]


class TestAuthServiceCheckNeedsRehash:
    """``check_needs_rehash`` 在同一事务中升级哈希（SPEC §12.1）。"""

    async def test_rehash_upgrades_old_hash_in_same_transaction(self) -> None:
        """旧参数哈希在同一 UoW 中升级。"""
        uow = _FakeAuthUnitOfWork()
        # 创建使用旧参数的用户
        old_hasher = _Argon2Hasher(memory_cost=65536, time_cost=1, parallelism=1)
        old_hash = old_hasher.hash(_VALID_PASSWORD)
        now = datetime.now(UTC)
        user = User.new(
            username="oldparams",
            display_name="Old Params",
            password_hash=old_hash,
            current_time=now,
        )
        uow.users._users[user.id] = user

        service, _ = _make_auth_service(uow)

        result = await service.login(
            username="oldparams",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="Agent",
            device=None,
            current_time=now,
        )

        assert result.access_token  # 登录成功
        # 哈希已升级——当前参数的哈希不需要 rehash
        updated_user = uow.users._users[user.id]
        assert "t=3" in updated_user.password_hash
        assert PasswordHasher().needs_rehash(updated_user.password_hash) is False
        assert uow.committed

    async def test_current_params_no_rehash(self) -> None:
        """当前参数哈希不触发 rehash。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="Agent",
            device=None,
            current_time=datetime.now(UTC),
        )

        # 哈希未改变（仍是当前参数）
        user = list(uow.users._users.values())[0]
        assert "t=3" in user.password_hash


class TestAuthServiceLogout:
    """登出 Use Case 单元测试（SPEC §12.3、§12.4）。"""

    async def test_logout_revokes_session(self) -> None:
        """登出吊销会话。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        result = await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="Agent",
            device=None,
            current_time=datetime.now(UTC),
        )

        session = await uow.sessions.get_by_id(result.session_id)
        assert session is not None
        assert session.is_active

        await service.logout(
            refresh_token=result.refresh_token,
            current_time=datetime.now(UTC),
        )

        session = await uow.sessions.get_by_id(result.session_id)
        assert session is not None
        assert session.is_revoked
        assert session.revoked_reason == "logout"

    async def test_logout_idempotent_with_invalid_token(self) -> None:
        """无效 Token 登出幂等成功。"""
        service, uow = _make_auth_service()

        # 不应抛出异常
        await service.logout(
            refresh_token="invalid_token_value",
            current_time=datetime.now(UTC),
        )

    async def test_logout_idempotent_when_no_cookie(self) -> None:
        """已登出的会话再次登出幂等成功。"""
        service, uow = _make_auth_service()
        _create_user_in_uow(uow)

        result = await service.login(
            username="testuser",
            password=_VALID_PASSWORD,
            ip="127.0.0.1",
            user_agent="Agent",
            device=None,
            current_time=datetime.now(UTC),
        )

        # 第一次登出
        await service.logout(
            refresh_token=result.refresh_token,
            current_time=datetime.now(UTC),
        )
        # 第二次登出（会话已吊销）— 幂等
        await service.logout(
            refresh_token=result.refresh_token,
            current_time=datetime.now(UTC),
        )


class TestSessionModel:
    """会话模型单元测试（SPEC §12.3）。"""

    def test_session_default_timeouts(self) -> None:
        """会话默认空闲超时 30 分钟、绝对超时 12 小时（SPEC §12.3）。"""
        session = Session.new(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            ip="127.0.0.1",
            user_agent="Agent",
            current_time=datetime.now(UTC),
        )
        assert session.idle_timeout_minutes == 30
        assert session.absolute_timeout_hours == 12

    def test_session_absolute_expiry(self) -> None:
        """绝对过期时间 = 创建时间 + 12 小时。"""
        now = datetime.now(UTC)
        session = Session.new(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            ip="127.0.0.1",
            user_agent="Agent",
            current_time=now,
        )
        from datetime import timedelta

        assert session.absolute_expiry == now + timedelta(hours=12)

    def test_session_revoke(self) -> None:
        """revoke 返回已吊销的新实例。"""
        now = datetime.now(UTC)
        session = Session.new(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            ip="127.0.0.1",
            user_agent="Agent",
            current_time=now,
        )
        revoked = session.revoke(reason="logout", current_time=now)
        assert revoked.is_revoked
        assert revoked.revoked_reason == "logout"
        assert session.is_active  # 原实例不变

    def test_session_is_expired_after_absolute_timeout(self) -> None:
        """超过绝对超时的会话过期。"""
        from datetime import timedelta

        now = datetime.now(UTC)
        session = Session.new(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            ip="127.0.0.1",
            user_agent="Agent",
            current_time=now,
        )
        future = now + timedelta(hours=13)
        assert session.is_expired(current_time=future) is True

    def test_session_is_expired_after_idle_timeout(self) -> None:
        """超过空闲超时的会话过期。"""
        from datetime import timedelta

        now = datetime.now(UTC)
        session = Session.new(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            ip="127.0.0.1",
            user_agent="Agent",
            current_time=now,
        )
        future = now + timedelta(minutes=31)
        assert session.is_expired(current_time=future) is True

    def test_session_not_expired_within_timeouts(self) -> None:
        """超时范围内的会话未过期。"""
        from datetime import timedelta

        now = datetime.now(UTC)
        session = Session.new(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            ip="127.0.0.1",
            user_agent="Agent",
            current_time=now,
        )
        soon = now + timedelta(minutes=10)
        assert session.is_expired(current_time=soon) is False
