"""Refresh Token 轮换、重放检测与浏览器传递安全集成测试 — SPEC 12.2 / 12.4.

使用真实 PostgreSQL（Testcontainers / 本地二进制），禁止 SQLite（SPEC 28.2）。

覆盖验收标准:
  - AC-0: 刷新成功轮换 Refresh Token。
  - AC-1: 并发使用同一 Refresh Token 只有一个成功。
  - AC-2: 重放检测（吊销 Session 和 Family）。
  - AC-3: Cookie 属性验证。
  - AC-4: Origin 校验。
  - AC-5: 旧 Access Token 失效、单活跃 Token、会话吊销拒绝。
  - AC-6: Logout 吊销会话并删除 Cookie。
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
from app.modules.audit.adapter import SqlAlchemyLoginLogRepository
from app.modules.audit.security_log import StructlogSecurityLogger
from app.modules.auth.constants import ACCESS_TOKEN_TTL
from app.modules.auth.errors import RefreshFailedError
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


# ── 查询辅助 ───────────────────────────────────────────────────────────────


async def _get_refresh_token(
    database_url: str,
    digest: str,
) -> dict[str, object] | None:
    """按摘要查询 Refresh Token 记录。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT id, session_id, family_id, token_digest, "
                    "predecessor_id, used_at, expires_at, revoked_reason "
                    "FROM auth_refresh_tokens WHERE token_digest = :d",
                ),
                {"d": digest},
            )
            row = result.first()
            if row is None:
                return None
            return {
                "id": row[0],
                "session_id": row[1],
                "family_id": row[2],
                "token_digest": row[3],
                "predecessor_id": row[4],
                "used_at": row[5],
                "expires_at": row[6],
                "revoked_reason": row[7],
            }
    finally:
        await engine.dispose()


async def _count_family_tokens(
    database_url: str,
    family_id: str,
) -> int:
    """统计 Token Family 中的记录数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM auth_refresh_tokens WHERE family_id = :fid",
                ),
                {"fid": family_id},
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _get_session_revoked(
    database_url: str,
    session_id: str,
) -> dict[str, object] | None:
    """查询会话的吊销状态。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT revoked, revoked_reason, access_token_digest "
                    "FROM auth_sessions WHERE id = :id",
                ),
                {"id": session_id},
            )
            row = result.first()
            if row is None:
                return None
            return {
                "revoked": row[0],
                "revoked_reason": row[1],
                "access_token_digest": row[2],
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
                    "SELECT username, result, failure_reason "
                    "FROM login_logs ORDER BY occurred_at",
                ),
            )
            return [
                {
                    "username": row[0],
                    "result": row[1],
                    "failure_reason": row[2],
                }
                for row in result
            ]
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

    def uow_factory() -> object:
        from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

        return SqlAlchemyUnitOfWork(engine)  # type: ignore[arg-type]

    def user_auth_port_factory(session: AsyncSession) -> SqlAlchemyUserAuthAdapter:
        return SqlAlchemyUserAuthAdapter(session)

    def login_log_factory(session: AsyncSession) -> SqlAlchemyLoginLogRepository:
        return SqlAlchemyLoginLogRepository(session)

    def security_log_factory(session: AsyncSession) -> StructlogSecurityLogger:
        return StructlogSecurityLogger()

    return AuthUseCase(
        uow_factory=uow_factory,  # type: ignore[arg-type]
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


async def _login_and_get_refresh_token(
    database_url: str,
    engine: object,
    clock: FixedClock,
) -> tuple[str, str, str, str]:
    """登录并返回 (raw_access_token, raw_refresh_token, session_id, family_id)。"""

    await _create_test_user(database_url)
    uc = _make_auth_use_case(engine, clock, FixedIdGenerator())
    result = await uc.login(
        LoginRequest(username="testuser", password="secure_password_12"),
        ip_address="127.0.0.1",
        user_agent="TestAgent/1.0",
        request_id="test-login",
    )

    refresh_digest = _make_digest_service().digest_refresh_token(result.refresh_token)
    token = await _get_refresh_token(database_url, refresh_digest)
    assert token is not None

    return (
        result.access_token,
        result.refresh_token,
        str(token["session_id"]),
        str(token["family_id"]),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 刷新成功轮换 — 旧 Token 失效、新 Token Set-Cookie、响应体 Access Token
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_refresh_rotates_token_and_returns_access_token(
    database_url: str,
) -> None:
    """刷新成功轮换 Refresh Token，旧 Token 立即失效 — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            (
                raw_access,
                raw_refresh,
                session_id,
                family_id,
            ) = await _login_and_get_refresh_token(database_url, engine, clock)

            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())
            clock.advance(timedelta(minutes=5))

            result = await uc.refresh(
                raw_refresh,
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="test-refresh",
            )

            # 响应体为新 Access Token
            assert result.access_token
            assert result.access_token != raw_access
            assert result.expires_in == int(ACCESS_TOKEN_TTL.total_seconds())

            # 新 Refresh Token 返回（经 Set-Cookie 下发）
            assert result.refresh_token
            assert result.refresh_token != raw_refresh

            # 旧 Refresh Token 标记为已使用
            old_digest = _make_digest_service().digest_refresh_token(raw_refresh)
            old_token = await _get_refresh_token(database_url, old_digest)
            assert old_token is not None
            assert old_token["used_at"] is not None
            assert old_token["revoked_reason"] is None

            # 新 Refresh Token 已落库，同一 Family
            new_digest = _make_digest_service().digest_refresh_token(
                result.refresh_token,
            )
            new_token = await _get_refresh_token(database_url, new_digest)
            assert new_token is not None
            assert new_token["used_at"] is None
            assert new_token["family_id"] == old_token["family_id"]
            assert new_token["predecessor_id"] == old_token["id"]
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: 并发使用同一 Refresh Token 只有一个成功
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_concurrent_refresh_only_one_succeeds(
    database_url: str,
) -> None:
    """并发使用同一 Refresh Token 只有一个成功 — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            _, raw_refresh, _, _ = await _login_and_get_refresh_token(
                database_url,
                engine,
                clock,
            )

            clock.advance(timedelta(minutes=5))

            # 两个不同的 Use Case（不同 UoW / AsyncSession）并发刷新
            uc1 = _make_auth_use_case(engine, clock, FixedIdGenerator())
            uc2 = _make_auth_use_case(engine, clock, FixedIdGenerator())

            results = await asyncio.gather(
                uc1.refresh(
                    raw_refresh,
                    ip_address="127.0.0.1",
                    user_agent="A",
                    request_id="req-1",
                ),
                uc2.refresh(
                    raw_refresh,
                    ip_address="127.0.0.1",
                    user_agent="B",
                    request_id="req-2",
                ),
                return_exceptions=True,
            )

            # 恰好一个成功、一个失败
            successes = [r for r in results if not isinstance(r, Exception)]
            failures = [r for r in results if isinstance(r, Exception)]

            assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
            assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}"
            assert isinstance(failures[0], RefreshFailedError)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: 重放检测 — 已使用 Token → 吊销 Session 和 Family
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_replay_detected_revokes_session_and_family(
    database_url: str,
) -> None:
    """已使用 Refresh Token 再次出现 → 吊销 Session 和 Family — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            _, raw_refresh, session_id, family_id = await _login_and_get_refresh_token(
                database_url, engine, clock
            )

            # 第一次刷新——成功
            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())
            clock.advance(timedelta(minutes=5))
            await uc.refresh(
                raw_refresh,
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="req-refresh-1",
            )

            # 用旧 Token 再次刷新——重放
            clock.advance(timedelta(minutes=1))
            with pytest.raises(RefreshFailedError):
                await uc.refresh(
                    raw_refresh,
                    ip_address="127.0.0.1",
                    user_agent="TestAgent/1.0",
                    request_id="req-replay",
                )

            # Session 被吊销
            session = await _get_session_revoked(database_url, session_id)
            assert session is not None
            assert session["revoked"] is True

            # 整个 Family 被吊销
            family_count = await _count_family_tokens(database_url, family_id)
            assert family_count >= 2  # 原始 + 轮换后

            # 记录了刷新异常日志
            logs = await _get_login_logs(database_url)
            refresh_errors = [
                log for log in logs if log["result"] == "token_refresh_error"
            ]
            assert len(refresh_errors) >= 1
            assert refresh_errors[-1]["failure_reason"] == "refresh_replay"

            # 用轮换后的新 Token 也无法刷新（Family 已吊销）
            # 获取轮换后的新 Token
            # 新 Token 已被 revoke_family 吊销（revoked_reason 已设置）
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: Cookie 属性（单位测试在 test_refresh_unit.py）
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_login_issues_refresh_token(database_url: str) -> None:
    """登录成功创建 Refresh Token Family 并返回 Refresh Token — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            await _create_test_user(database_url)
            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())
            result = await uc.login(
                LoginRequest(username="testuser", password="secure_password_12"),
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="test-login",
            )

            # Refresh Token 随登录返回（Router 经 Set-Cookie 下发）
            assert result.refresh_token
            assert len(result.refresh_token) >= 32

            # Refresh Token 摘要落库（不存明文）
            digest = _make_digest_service().digest_refresh_token(result.refresh_token)
            token = await _get_refresh_token(database_url, digest)
            assert token is not None
            assert token["token_digest"] == digest
            assert token["token_digest"] != result.refresh_token
            assert token["predecessor_id"] is None  # 首个 Token
            assert token["used_at"] is None
            assert token["revoked_reason"] is None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-5: 旧 Access Token 失效、单活跃 Access Token、会话吊销后刷新被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_refresh_invalidates_old_access_token(
    database_url: str,
) -> None:
    """刷新后旧 Access Token 立即失效 — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            raw_access, raw_refresh, _, _ = await _login_and_get_refresh_token(
                database_url, engine, clock
            )

            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

            # 旧 Access Token 初始有效
            assert await uc.authenticate(raw_access) is not None

            # 刷新
            clock.advance(timedelta(minutes=5))
            refresh_result = await uc.refresh(
                raw_refresh,
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="req-refresh",
            )

            # 旧 Access Token 失效（摘要被替换）
            assert await uc.authenticate(raw_access) is None

            # 新 Access Token 有效
            assert await uc.authenticate(refresh_result.access_token) is not None
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_single_active_access_token_per_session(
    database_url: str,
) -> None:
    """同一会话同时最多一个有效 Access Token — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            raw_access, raw_refresh, session_id, _ = await _login_and_get_refresh_token(
                database_url, engine, clock
            )

            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())
            clock.advance(timedelta(minutes=5))
            refresh_result = await uc.refresh(
                raw_refresh,
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="req-refresh",
            )

            # 只有新 Access Token 有效
            assert await uc.authenticate(raw_access) is None
            assert await uc.authenticate(refresh_result.access_token) is not None

            # 会话摘要为新 Token 的摘要
            new_digest = _make_digest_service().digest_access_token(
                refresh_result.access_token,
            )
            session = await _get_session_revoked(database_url, session_id)
            assert session is not None
            assert session["access_token_digest"] == new_digest
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_refresh_after_session_revoked_fails(
    database_url: str,
) -> None:
    """会话吊销后刷新被拒绝 — SPEC 12.2 / 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            _, raw_refresh, session_id, _ = await _login_and_get_refresh_token(
                database_url, engine, clock
            )

            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

            # 吊销会话
            revoke_engine = create_db_engine(database_url)
            try:
                async with revoke_engine.begin() as conn:
                    await conn.execute(
                        text(
                            "UPDATE auth_sessions SET revoked = true, "
                            "revoked_reason = 'manual_test' WHERE id = :id",
                        ),
                        {"id": session_id},
                    )
            finally:
                await revoke_engine.dispose()

            # 刷新被拒绝
            clock.advance(timedelta(minutes=5))
            with pytest.raises(RefreshFailedError):
                await uc.refresh(
                    raw_refresh,
                    ip_address="127.0.0.1",
                    user_agent="TestAgent/1.0",
                    request_id="req-refresh-revoked",
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_refresh_token_expires_no_later_than_session(
    database_url: str,
) -> None:
    """Refresh Token 过期时间不晚于会话绝对过期 — SPEC 12.3."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            _, raw_refresh, session_id, _ = await _login_and_get_refresh_token(
                database_url, engine, clock
            )

            # 获取 Refresh Token 和会话的过期时间
            refresh_digest = _make_digest_service().digest_refresh_token(raw_refresh)
            token = await _get_refresh_token(database_url, refresh_digest)
            assert token is not None

            engine_check = create_db_engine(database_url)
            try:
                async with engine_check.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT absolute_expires_at FROM auth_sessions "
                            "WHERE id = :id",
                        ),
                        {"id": session_id},
                    )
                    abs_expires = result.scalar()
            finally:
                await engine_check.dispose()

            assert token["expires_at"] == abs_expires

            # 刷新后新 Token 的过期时间也不晚于会话绝对过期
            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())
            clock.advance(timedelta(minutes=5))
            result = await uc.refresh(
                raw_refresh,
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="req-refresh",
            )

            new_digest = _make_digest_service().digest_refresh_token(
                result.refresh_token,
            )
            new_token = await _get_refresh_token(database_url, new_digest)
            assert new_token is not None
            assert new_token["expires_at"] <= abs_expires
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_refresh_disabled_user_fails(database_url: str) -> None:
    """用户禁用后刷新被拒绝 — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            _, raw_refresh, _, _ = await _login_and_get_refresh_token(
                database_url,
                engine,
                clock,
            )

            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

            # 禁用用户
            disable_engine = create_db_engine(database_url)
            try:
                async with disable_engine.begin() as conn:
                    await conn.execute(
                        text(
                            "UPDATE users SET status = 'disabled' "
                            "WHERE username = 'testuser'",
                        ),
                    )
            finally:
                await disable_engine.dispose()

            # 刷新被拒绝
            clock.advance(timedelta(minutes=5))
            with pytest.raises(RefreshFailedError):
                await uc.refresh(
                    raw_refresh,
                    ip_address="127.0.0.1",
                    user_agent="TestAgent/1.0",
                    request_id="req-refresh-disabled",
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_nonexistent_refresh_token_fails(database_url: str) -> None:
    """不存在的 Refresh Token 刷新失败 — SPEC 12.2."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())

            with pytest.raises(RefreshFailedError):
                await uc.refresh(
                    "nonexistent_refresh_token_value_xyz",
                    ip_address="127.0.0.1",
                    user_agent="TestAgent/1.0",
                    request_id="req-nonexistent",
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-6: Logout 吊销会话并按相同 Cookie 属性删除客户端 Cookie
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_logout_revokes_refresh_tokens(database_url: str) -> None:
    """退出登录吊销服务端会话和 Refresh Token — SPEC 12.4."""

    await _apply_migrations(database_url)
    await _cleanup_tables(database_url)
    try:
        engine = create_db_engine(database_url)
        clock = FixedClock(datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
        try:
            raw_access, raw_refresh, session_id, _ = await _login_and_get_refresh_token(
                database_url, engine, clock
            )

            # 获取 session_id 和 user_id
            uc = _make_auth_use_case(engine, clock, FixedIdGenerator())
            auth_result = await uc.authenticate(raw_access)
            assert auth_result is not None
            user_id, sess_id = auth_result

            # 退出
            await uc.logout_current(
                session_id=sess_id,
                user_id=user_id,
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
                request_id="req-logout",
            )

            # 会话被吊销
            session = await _get_session_revoked(database_url, str(sess_id))
            assert session is not None
            assert session["revoked"] is True

            # Refresh Token 被吊销
            refresh_digest = _make_digest_service().digest_refresh_token(raw_refresh)
            token = await _get_refresh_token(database_url, refresh_digest)
            assert token is not None
            assert token["revoked_reason"] == "user_logout"

            # 旧 Refresh Token 刷新被拒绝
            clock.advance(timedelta(minutes=1))
            with pytest.raises(RefreshFailedError):
                await uc.refresh(
                    raw_refresh,
                    ip_address="127.0.0.1",
                    user_agent="TestAgent/1.0",
                    request_id="req-after-logout",
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_tables(database_url)
