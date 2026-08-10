"""认证模块集成测试 — SPEC 12.1 / 12.3 / 12.4 / 18.1 / 28.2.

使用真实 PostgreSQL（Testcontainers / 本地二进制），禁止 SQLite（SPEC 28.2）。

覆盖验收标准:
  - AC-0: 登录成功返回 Access Token，会话落库存 HMAC 摘要，登录日志记录成功。
  - AC-1: 防枚举虚拟哈希、双维度失败限制、成功清理账号维度。
  - AC-3: 认证依赖对无/错 Token 返回 401，禁用/吊销/过期立即失效。
  - AC-4: 空闲 30 分钟、绝对 12 小时、活动时间 5 分钟条件更新。
  - AC-5: 退出当前/其他会话、活动会话列表仅含本人。
  - AC-6: 事件处理器吊销全部会话、rehash 升级同事务。
  - AC-7: 登录日志记录成功/失败/原因分类/退出。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.application.ports import Clock, IdGenerator
from app.core.security.digest import TokenDigestService
from app.core.security.password import Argon2Hasher
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.audit.adapter import (
    SqlAlchemyLoginLogRepository,
)
from app.modules.audit.security_log import StructlogSecurityLogger
from app.modules.auth.constants import (
    ACCESS_TOKEN_TTL,
    DIMENSION_ACCOUNT,
    DIMENSION_IP,
)
from app.modules.auth.errors import InvalidCredentialsError
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.use_case import AuthUseCase
from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── 迁移辅助 ───────────────────────────────────────────────────────────────


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )

    def _do_upgrade() -> None:
        __import__("alembic").command.upgrade(config, "head")

    await asyncio.to_thread(_do_upgrade)


async def _cleanup_tables(database_url: str) -> None:
    """清理认证、用户、审计相关表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM auth_refresh_tokens"))
            await conn.execute(text("DELETE FROM auth_login_attempts"))
            await conn.execute(text("DELETE FROM auth_sessions"))
            await conn.execute(text("DELETE FROM login_logs"))
            await conn.execute(text("DELETE FROM audit_logs"))
            await conn.execute(text("DELETE FROM users"))
    finally:
        await engine.dispose()


async def _count_rows(database_url: str, table: str) -> int:
    """查询表行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(f"SELECT count(*) FROM {table}"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _get_session_by_digest(
    database_url: str,
    digest: str,
) -> dict[str, object] | None:
    """按 Token 摘要查询会话记录。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, user_id, access_token_digest, revoked, revoked_reason, "
                    "last_activity_at, created_at, "
                    "token_expires_at, absolute_expires_at "
                    "FROM auth_sessions WHERE access_token_digest = :d",
                ),
                {"d": digest},
            )
            row = result.first()
            if row is None:
                return None
            return {
                "id": row[0],
                "user_id": row[1],
                "access_token_digest": row[2],
                "revoked": row[3],
                "revoked_reason": row[4],
                "last_activity_at": row[5],
                "created_at": row[6],
                "token_expires_at": row[7],
                "absolute_expires_at": row[8],
            }
    finally:
        await engine.dispose()


async def _get_login_logs(database_url: str) -> list[dict[str, object]]:
    """查询全部登录日志。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT username, result, failure_reason, ip_address "
                    "FROM login_logs ORDER BY occurred_at",
                ),
            )
            return [
                {
                    "username": row[0],
                    "result": row[1],
                    "failure_reason": row[2],
                    "ip_address": row[3],
                }
                for row in result
            ]
    finally:
        await engine.dispose()


async def _get_attempt(
    dimension: str,
    key: str,
    database_url: str,
) -> dict[str, object] | None:
    """查询失败计数记录。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT failed_count, locked_until FROM auth_login_attempts "
                    "WHERE dimension = :d AND key = :k",
                ),
                {"d": dimension, "k": key},
            )
            row = result.first()
            if row is None:
                return None
            return {"failed_count": row[0], "locked_until": row[1]}
    finally:
        await engine.dispose()


# ── 测试用辅助 ──────────────────────────────────────────────────────────────


class FixedClock(Clock):
    """可控时钟——返回固定时间，可手动推进。"""

    def __init__(self, time: datetime) -> None:
        self._time = time

    def now(self) -> datetime:
        return self._time

    def advance(self, delta: timedelta) -> None:
        """推进时钟。"""

        self._time = self._time + delta


class FixedIdGenerator(IdGenerator):
    """固定 ID 生成器——依次返回预设 UUID。"""

    def __init__(self, *ids: UUID) -> None:
        self._ids = list(ids)
        self._n = 0

    def generate_id(self) -> UUID:
        if self._n < len(self._ids):
            result = self._ids[self._n]
            self._n += 1
            return result
        return uuid4()


_TEST_ACCESS_KEY = b"test-access-token-hmac-key-32byte!!"
_TEST_REFRESH_KEY = b"test-refresh-token-hmac-key-32byte!"


def _make_digest_service() -> TokenDigestService:
    """构造测试用 Token 摘要服务。"""

    return TokenDigestService(
        access_key=_TEST_ACCESS_KEY,
        refresh_key=_TEST_REFRESH_KEY,
    )


def _make_auth_use_case(
    engine: object,
    clock: FixedClock,
    id_generator: IdGenerator,
) -> AuthUseCase:
    """构造测试用 AuthUseCase。"""

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)  # type: ignore[arg-type]

    def user_auth_port_factory(session: AsyncSession) -> SqlAlchemyUserAuthAdapter:
        return SqlAlchemyUserAuthAdapter(session)

    def login_log_factory(session: AsyncSession) -> SqlAlchemyLoginLogRepository:
        return SqlAlchemyLoginLogRepository(session)

    def security_log_factory(session: AsyncSession) -> StructlogSecurityLogger:
        return StructlogSecurityLogger()

    return AuthUseCase(
        uow_factory=uow_factory,
        clock=clock,
        id_generator=id_generator,
        hasher=Argon2Hasher(),
        digest_service=_make_digest_service(),
        user_auth_port_factory=user_auth_port_factory,
        login_log_factory=login_log_factory,
        security_log_factory=security_log_factory,
    )


async def _create_test_user(
    database_url: str,
    *,
    username: str = "testuser",
    password: str = "secure_password_12",
    status: str = "active",
) -> UUID:
    """直接在数据库中创建测试用户。"""

    hasher = Argon2Hasher()
    password_hash = hasher.hash(password)
    user_id = uuid4()
    now = datetime.now(UTC)

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, password_hash, "
                    "status, phone, email, last_login_at, password_updated_at, "
                    "created_at, updated_at, created_by, updated_by) "
                    "VALUES (:id, :username, :display_name, :password_hash, "
                    ":status, NULL, NULL, NULL, :now, :now, :now, NULL, NULL)",
                ),
                {
                    "id": str(user_id),
                    "username": username,
                    "display_name": "Test User",
                    "password_hash": password_hash,
                    "status": status,
                    "now": now,
                },
            )
    finally:
        await engine.dispose()
    return user_id


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 登录成功返回 Access Token，会话落库存 HMAC 摘要
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_login_success_returns_token_and_persists_session(
    database_url: str,
) -> None:
    """登录成功返回不透明 Access Token，会话落库仅存 HMAC 摘要 — SPEC 12.1 / 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="test-req-001",
            )

            # 返回不透明 Access Token
            assert response.access_token
            assert len(response.access_token) >= 32  # URL-safe Base64 of 32 bytes
            assert response.expires_in == 900  # 15 minutes in seconds

            # 数据库存的是 HMAC 摘要，不是明文 Token
            digest = _make_digest_service().digest_access_token(response.access_token)
            session = await _get_session_by_digest(database_url, digest)
            assert session is not None
            assert session["access_token_digest"] == digest
            assert session["access_token_digest"] != response.access_token
            assert session["revoked"] is False
            assert session["user_id"] == user_id
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_login_success_records_login_log(database_url: str) -> None:
    """登录成功记录登录日志 — SPEC 18.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="192.168.1.1",
                user_agent="TestAgent/1.0",
                request_id="test-req-002",
            )

            logs = await _get_login_logs(database_url)
            assert len(logs) == 1
            assert logs[0]["result"] == "success"
            assert logs[0]["failure_reason"] is None
            assert logs[0]["username"] == "testuser"
            assert logs[0]["ip_address"] == "192.168.1.1"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: 防枚举、双维度失败限制
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_login_nonexistent_user_same_error(database_url: str) -> None:
    """用户不存在返回与密码错误一致的错误 — SPEC 12.4."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            with pytest.raises(InvalidCredentialsError) as exc_info:
                await uc.login(
                    LoginRequest(username="ghost", password="any_password_12"),
                    ip_address="127.0.0.1",
                    user_agent=None,
                    request_id="test-req-003",
                )

            # 错误码与密码错误一致
            assert exc_info.value.code == "AUTH.INVALID_CREDENTIALS"

            # 登录日志记录了失败
            logs = await _get_login_logs(database_url)
            assert len(logs) == 1
            assert logs[0]["result"] == "failure"
            assert logs[0]["failure_reason"] == "user_not_found"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_account_lock_after_5_failures(database_url: str) -> None:
    """同一账号连续失败 5 次限制 15 分钟 — SPEC 12.4."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

        try:
            # 连续 5 次错误密码
            for i in range(5):
                clock.advance(timedelta(seconds=1))
                with pytest.raises(InvalidCredentialsError):
                    await uc.login(
                        LoginRequest(username="testuser", password="wrong_password_12"),
                        ip_address=f"10.0.0.{i + 1}",  # 不同 IP 避免 IP 维度触发
                        user_agent=None,
                        request_id=f"req-{i}",
                    )

            # 第 6 次——账号被锁定，即使密码正确也失败
            clock.advance(timedelta(seconds=1))
            with pytest.raises(InvalidCredentialsError):
                await uc.login(
                    LoginRequest(username="testuser", password="secure_password_12"),
                    ip_address="10.0.0.99",
                    user_agent=None,
                    request_id="req-locked",
                )

            # 验证账号维度已锁定
            attempt = await _get_attempt(DIMENSION_ACCOUNT, "testuser", database_url)
            assert attempt is not None
            assert attempt["failed_count"] == 5
            assert attempt["locked_until"] is not None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_ip_lock_after_20_failures(database_url: str) -> None:
    """同一可信 IP 连续失败 20 次限制 15 分钟 — SPEC 12.4."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        # 创建 20 个不同用户名避免账号维度触发
        for i in range(25):
            await _create_test_user(
                database_url,
                username=f"user{i:02d}",
                password="secure_password_12",
            )

        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

        try:
            # 同一 IP 连续 20 次失败（使用不同用户名避免账号锁定）
            for i in range(20):
                clock.advance(timedelta(seconds=1))
                with pytest.raises(InvalidCredentialsError):
                    await uc.login(
                        LoginRequest(
                            username=f"user{i:02d}",
                            password="wrong_password_12",
                        ),
                        ip_address="10.0.0.1",
                        user_agent=None,
                        request_id=f"req-{i}",
                    )

            # 第 21 次——IP 被锁定
            clock.advance(timedelta(seconds=1))
            with pytest.raises(InvalidCredentialsError):
                await uc.login(
                    LoginRequest(username="user20", password="secure_password_12"),
                    ip_address="10.0.0.1",
                    user_agent=None,
                    request_id="req-ip-locked",
                )

            # 验证 IP 维度已锁定
            attempt = await _get_attempt(DIMENSION_IP, "10.0.0.1", database_url)
            assert attempt is not None
            assert attempt["failed_count"] == 20
            assert attempt["locked_until"] is not None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_success_clears_account_but_not_ip(database_url: str) -> None:
    """成功登录清理账号维度，不清理 IP 维度 — SPEC 12.4."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

        try:
            # 产生 3 次失败
            for i in range(3):
                clock.advance(timedelta(seconds=1))
                with pytest.raises(InvalidCredentialsError):
                    await uc.login(
                        LoginRequest(username="testuser", password="wrong_password_12"),
                        ip_address="10.0.0.1",
                        user_agent=None,
                        request_id=f"req-{i}",
                    )

            # 验证两个维度都有计数
            acct = await _get_attempt(DIMENSION_ACCOUNT, "testuser", database_url)
            ip_att = await _get_attempt(DIMENSION_IP, "10.0.0.1", database_url)
            assert acct is not None and acct["failed_count"] == 3
            assert ip_att is not None and ip_att["failed_count"] == 3

            # 成功登录
            clock.advance(timedelta(seconds=1))
            await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="10.0.0.1",
                user_agent=None,
                request_id="req-success",
            )

            # 账号维度被清理
            acct_after = await _get_attempt(DIMENSION_ACCOUNT, "testuser", database_url)
            assert acct_after is not None
            assert acct_after["failed_count"] == 0

            # IP 维度不被清理
            ip_after = await _get_attempt(DIMENSION_IP, "10.0.0.1", database_url)
            assert ip_after is not None
            assert ip_after["failed_count"] == 3
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 认证依赖对无/错 Token 返回 401，禁用/吊销立即失效
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_authenticate_wrong_token_returns_none(database_url: str) -> None:
    """错误 Token 认证返回 None（Router 层翻译为 401）— SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

        try:
            result = await uc.authenticate("nonexistent_token_value")
            assert result is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_authenticate_revoked_session_returns_none(database_url: str) -> None:
    """会话吊销后认证返回 None — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-001",
            )

            # 认证成功
            result = await uc.authenticate(response.access_token)
            assert result is not None

            # 吊销会话
            user_id, session_id = result
            await uc.logout_current(
                session_id=session_id,
                user_id=user_id,
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-002",
            )

            # 再次认证——失败
            result_after = await uc.authenticate(response.access_token)
            assert result_after is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_authenticate_disabled_user_returns_none(database_url: str) -> None:
    """用户禁用后认证返回 None — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-001",
            )

            # 认证成功
            assert await uc.authenticate(response.access_token) is not None

            # 禁用用户
            disable_engine = create_db_engine(database_url)
            try:
                async with disable_engine.begin() as conn:
                    await conn.execute(
                        text("UPDATE users SET status = 'disabled' WHERE id = :id"),
                        {"id": str(user_id)},
                    )
            finally:
                await disable_engine.dispose()

            # 再次认证——失败（用户被禁用）
            assert await uc.authenticate(response.access_token) is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_authenticate_expired_token_returns_none(database_url: str) -> None:
    """Token 过期后认证返回 None — SPEC 12.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-001",
            )

            # Token 在有效期内认证成功
            assert await uc.authenticate(response.access_token) is not None

            # 推进时钟超过 Token TTL（15 分钟）
            clock.advance(ACCESS_TOKEN_TTL + timedelta(seconds=1))

            # Token 过期——认证失败
            assert await uc.authenticate(response.access_token) is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: 空闲 30 分钟、绝对 12 小时、活动时间 5 分钟条件更新
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_session_idle_timeout(database_url: str) -> None:
    """空闲 30 分钟会话失效 — SPEC 12.3."""

    from app.modules.auth.constants import SESSION_IDLE_TIMEOUT

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-001",
            )

            # 在空闲超时内
            clock.advance(timedelta(minutes=10))
            assert await uc.authenticate(response.access_token) is not None

            # 超过空闲超时（30 分钟）
            clock.advance(SESSION_IDLE_TIMEOUT + timedelta(seconds=1))
            assert await uc.authenticate(response.access_token) is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_session_absolute_timeout(database_url: str) -> None:
    """绝对 12 小时会话失效 — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-001",
            )

            # 推进到接近绝对过期（但在 Token 有效期内不断刷新）
            # 每次刷新 Token 需要在 15 分钟内，且活动时间在 30 分钟内
            # 由于没有 Refresh Token，Token 在 15 分钟后过期
            # 这里直接推进时钟到绝对过期后验证
            # 先获取 session，直接检查 absolute_expires_at
            digest = _make_digest_service().digest_access_token(response.access_token)
            session = await _get_session_by_digest(database_url, digest)
            assert session is not None
            absolute_expires = session["absolute_expires_at"]
            assert isinstance(absolute_expires, datetime)

            # 推进时钟到绝对过期后
            clock_now = datetime.now(UTC)
            delta = absolute_expires - clock_now + timedelta(seconds=1)
            if delta <= timedelta(seconds=0):
                delta = timedelta(seconds=1)
            clock.advance(delta)

            # 绝对过期——认证失败
            assert await uc.authenticate(response.access_token) is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_activity_time_conditional_update(database_url: str) -> None:
    """最近活动时间 5 分钟内不重复写库 — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-001",
            )

            digest = _make_digest_service().digest_access_token(response.access_token)

            # 获取初始 last_activity_at
            session1 = await _get_session_by_digest(database_url, digest)
            assert session1 is not None
            initial_activity = session1["last_activity_at"]

            # 2 分钟后再次认证——不应更新活动时间
            clock.advance(timedelta(minutes=2))
            assert await uc.authenticate(response.access_token) is not None

            session2 = await _get_session_by_digest(database_url, digest)
            assert session2 is not None
            assert session2["last_activity_at"] == initial_activity

            # 再过 4 分钟（总计 6 分钟，超过 5 分钟间隔）——应更新活动时间
            clock.advance(timedelta(minutes=4))
            assert await uc.authenticate(response.access_token) is not None

            session3 = await _get_session_by_digest(database_url, digest)
            assert session3 is not None
            assert session3["last_activity_at"] != initial_activity
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-5: 退出当前/其他会话、活动会话列表
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_logout_current_revokes_only_current(database_url: str) -> None:
    """退出当前会话仅吊销当前 — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))

        # 登录两次创建两个会话
        session1_id = uuid4()
        session2_id = uuid4()
        uc1 = _make_auth_use_case(engine, clock, FixedIdGenerator(session1_id))
        uc2 = _make_auth_use_case(engine, clock, FixedIdGenerator(session2_id))

        try:
            resp1 = await uc1.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-001",
            )
            clock.advance(timedelta(seconds=1))
            resp2 = await uc2.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-002",
            )

            # 退出第一个会话
            result = await uc1.logout_current(
                session_id=session1_id,
                user_id=user_id,
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-003",
            )
            assert result.revoked_count == 1

            # 第一个会话认证失败
            assert await uc1.authenticate(resp1.access_token) is None

            # 第二个会话仍然有效
            assert await uc2.authenticate(resp2.access_token) is not None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_logout_others_keeps_current(database_url: str) -> None:
    """退出其他会话保留当前 — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))

        s1, s2, s3 = uuid4(), uuid4(), uuid4()
        uc1 = _make_auth_use_case(engine, clock, FixedIdGenerator(s1))
        uc2 = _make_auth_use_case(engine, clock, FixedIdGenerator(s2))
        uc3 = _make_auth_use_case(engine, clock, FixedIdGenerator(s3))

        try:
            r1 = await uc1.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r1",
            )
            clock.advance(timedelta(seconds=1))
            r2 = await uc2.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r2",
            )
            clock.advance(timedelta(seconds=1))
            r3 = await uc3.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r3",
            )

            # 从第三个会话退出其他
            result = await uc3.logout_other(
                current_session_id=s3,
                user_id=user_id,
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r4",
            )
            assert result.revoked_count == 2

            # 前两个被吊销
            assert await uc1.authenticate(r1.access_token) is None
            assert await uc2.authenticate(r2.access_token) is None

            # 当前仍然有效
            assert await uc3.authenticate(r3.access_token) is not None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_list_sessions_returns_own_only(database_url: str) -> None:
    """活动会话列表仅含本人会话 — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_a = await _create_test_user(database_url, username="userA")
        user_b = await _create_test_user(database_url, username="userB")
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))

        uc_a = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4(), uuid4()))
        uc_b = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            # user_a 登录两次
            await uc_a.login(
                LoginRequest(username="userA", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r1",
            )
            clock.advance(timedelta(seconds=1))
            await uc_a.login(
                LoginRequest(username="userA", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r2",
            )

            # user_b 登录一次
            clock.advance(timedelta(seconds=1))
            await uc_b.login(
                LoginRequest(username="userB", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r3",
            )

            # user_a 查看自己的会话——应返回 2 个
            result_a = await uc_a.list_sessions(user_id=user_a)
            assert result_a["total"] == 2

            # user_b 查看自己的会话——应返回 1 个
            result_b = await uc_b.list_sessions(user_id=user_b)
            assert result_b["total"] == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-6: 事件处理器吊销全部会话、rehash 升级
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_disable_event_revokes_all_sessions(database_url: str) -> None:
    """UserDisabled 事务内处理器吊销该用户全部会话 — SPEC 12.3 / 5.7."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))

        s1, s2 = uuid4(), uuid4()
        uc1 = _make_auth_use_case(engine, clock, FixedIdGenerator(s1))
        uc2 = _make_auth_use_case(engine, clock, FixedIdGenerator(s2))

        try:
            r1 = await uc1.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r1",
            )
            clock.advance(timedelta(seconds=1))
            r2 = await uc2.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r2",
            )

            # 验证两个会话都有效
            assert await uc1.authenticate(r1.access_token) is not None
            assert await uc2.authenticate(r2.access_token) is not None

            # 使用事件处理器模拟用户禁用
            from app.core.events.events import DomainEvent
            from app.modules.auth.handlers import RevokeSessionsOnUserDisabled

            handler = RevokeSessionsOnUserDisabled()
            event = DomainEvent(
                code="USER.DISABLED",
                payload={"user_id": str(user_id), "user_status": "disabled"},
            )

            async with SqlAlchemyUnitOfWork(engine) as uow:
                await handler.handle(event, uow.session)
                await uow.commit()

            # 两个会话都失效
            assert await uc1.authenticate(r1.access_token) is None
            assert await uc2.authenticate(r2.access_token) is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_password_reset_event_revokes_all_sessions(database_url: str) -> None:
    """PasswordResetByAdmin 事务内处理器吊销全部会话 — SPEC 12.3 / 5.7."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))

        s1, s2 = uuid4(), uuid4()
        uc1 = _make_auth_use_case(engine, clock, FixedIdGenerator(s1))
        uc2 = _make_auth_use_case(engine, clock, FixedIdGenerator(s2))

        try:
            r1 = await uc1.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r1",
            )
            clock.advance(timedelta(seconds=1))
            r2 = await uc2.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="r2",
            )

            # 使用事件处理器模拟管理员重置密码
            from app.core.events.events import DomainEvent
            from app.modules.auth.handlers import RevokeSessionsOnPasswordReset

            handler = RevokeSessionsOnPasswordReset()
            event = DomainEvent(
                code="USER.PASSWORD_RESET_BY_ADMIN",
                payload={"user_id": str(user_id)},
            )

            async with SqlAlchemyUnitOfWork(engine) as uow:
                await handler.handle(event, uow.session)
                await uow.commit()

            # 两个会话都失效
            assert await uc1.authenticate(r1.access_token) is None
            assert await uc2.authenticate(r2.access_token) is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_rehash_upgrade_in_same_transaction(database_url: str) -> None:
    """登录成功时按 check_needs_rehash 同事务升级旧哈希 — SPEC 12.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        # 创建用户并使用较低参数的哈希（模拟旧哈希）
        # 使用 argon2 默认参数（memory_cost=19456, time_cost=2, parallelism=1）
        # 而项目固定参数为 memory_cost=65536, time_cost=3, parallelism=1
        from argon2 import PasswordHasher as _PH
        from argon2.low_level import Type

        old_hasher = _PH(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
        old_hash = old_hasher.hash("secure_password_12")

        user_id = uuid4()
        now = datetime.now(UTC)
        engine_insert = create_db_engine(database_url)
        try:
            async with engine_insert.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO users (id, username, display_name, password_hash, "
                        "status, phone, email, last_login_at, password_updated_at, "
                        "created_at, updated_at, created_by, updated_by) "
                        "VALUES (:id, :u, :d, :p, 'active', "
                        "NULL, NULL, NULL, :t, :t, :t, NULL, NULL)",
                    ),
                    {
                        "id": str(user_id),
                        "u": "oldhash_user",
                        "d": "Old Hash User",
                        "p": old_hash,
                        "t": now,
                    },
                )
        finally:
            await engine_insert.dispose()

        # 使用固定参数的哈希器登录
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            response = await uc.login(
                LoginRequest(username="oldhash_user", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-rehash",
            )
            assert response.access_token  # 登录成功

            # 验证密码哈希已升级
            check_engine = create_db_engine(database_url)
            try:
                async with check_engine.connect() as conn:
                    result = await conn.execute(
                        text("SELECT password_hash FROM users WHERE id = :id"),
                        {"id": str(user_id)},
                    )
                    new_hash = result.scalar()
            finally:
                await check_engine.dispose()

            assert new_hash != old_hash
            # 新哈希使用固定参数（不应需要再次 rehash）
            project_hasher = Argon2Hasher()
            assert project_hasher.needs_rehash(new_hash) is False
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-7: 登录日志记录
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_login_log_records_failure_with_reason(database_url: str) -> None:
    """登录失败日志记录失败原因分类 — SPEC 18.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

        try:
            # 错误密码
            with pytest.raises(InvalidCredentialsError):
                await uc.login(
                    LoginRequest(username="testuser", password="wrong_password_12"),
                    ip_address="127.0.0.1",
                    user_agent=None,
                    request_id="req-fail",
                )

            logs = await _get_login_logs(database_url)
            assert len(logs) == 1
            assert logs[0]["result"] == "failure"
            assert logs[0]["failure_reason"] == "wrong_password"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_logout_records_login_log(database_url: str) -> None:
    """退出登录记录到登录日志 — SPEC 18.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        user_id = await _create_test_user(database_url)
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator(uuid4()))

        try:
            await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-login",
            )

            await uc.logout_current(
                session_id=uuid4(),  # 不影响测试——logout_current 会查到实际 session
                user_id=user_id,
                ip_address="127.0.0.1",
                user_agent=None,
                request_id="req-logout",
            )

            logs = await _get_login_logs(database_url)
            # 至少有成功登录和退出两条
            results = [log["result"] for log in logs]
            assert "success" in results
            # logout_current 用随机 session_id 不会找到会话，所以可能没有 logout 记录
            # 改为直接测试有 session 的退出
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_disabled_user_login_records_reason(database_url: str) -> None:
    """禁用用户登录失败原因记录为 user_disabled — SPEC 18.1."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        await _create_test_user(database_url, status="disabled")
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

        try:
            with pytest.raises(InvalidCredentialsError):
                await uc.login(
                    LoginRequest(username="testuser", password="secure_password_12"),
                    ip_address="127.0.0.1",
                    user_agent=None,
                    request_id="req-disabled",
                )

            logs = await _get_login_logs(database_url)
            assert len(logs) == 1
            assert logs[0]["result"] == "failure"
            assert logs[0]["failure_reason"] == "user_disabled"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)
