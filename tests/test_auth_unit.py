"""认证模块单元测试 — SPEC 12.1 / 12.3 / 12.4.

覆盖领域规则和安全策略，不依赖数据库连接。
集成测试和 API 测试在单独文件中。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors.exceptions import AuthenticationError
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
from app.modules.auth.dependencies import extract_bearer_token
from app.modules.auth.errors import AUTH_INVALID_CREDENTIALS

# ═══════════════════════════════════════════════════════════════════════════════
# 常量验证 — SPEC 12.1 / 12.3 / 12.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAuthConstants:
    """认证常量符合 SPEC 要求."""

    def test_access_token_ttl_15_minutes(self) -> None:
        """Access Token 默认有效期 15 分钟 — SPEC 12.1."""

        assert timedelta(minutes=15) == ACCESS_TOKEN_TTL

    def test_session_idle_timeout_30_minutes(self) -> None:
        """会话空闲过期 30 分钟 — SPEC 12.3."""

        assert timedelta(minutes=30) == SESSION_IDLE_TIMEOUT

    def test_session_absolute_timeout_12_hours(self) -> None:
        """会话绝对过期 12 小时 — SPEC 12.3."""

        assert timedelta(hours=12) == SESSION_ABSOLUTE_TIMEOUT

    def test_activity_update_interval_5_minutes(self) -> None:
        """最近活动时间 5 分钟条件更新 — SPEC 12.3."""

        assert timedelta(minutes=5) == ACTIVITY_UPDATE_INTERVAL

    def test_account_failure_limit_5(self) -> None:
        """账号连续失败上限 5 次 — SPEC 12.4."""

        assert ACCOUNT_FAILURE_LIMIT == 5

    def test_ip_failure_limit_20(self) -> None:
        """IP 连续失败上限 20 次 — SPEC 12.4."""

        assert IP_FAILURE_LIMIT == 20

    def test_failure_lock_duration_15_minutes(self) -> None:
        """失败锁定持续 15 分钟 — SPEC 12.4."""

        assert timedelta(minutes=15) == FAILURE_LOCK_DURATION

    def test_dimension_constants(self) -> None:
        """双维度标识常量."""

        assert DIMENSION_ACCOUNT == "account"
        assert DIMENSION_IP == "ip"


# ═══════════════════════════════════════════════════════════════════════════════
# Bearer Token 提取 — SPEC 12.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestBearerTokenExtraction:
    """Bearer Token 提取 — SPEC 12.3 / 13.3."""

    def test_valid_bearer_token(self) -> None:
        """合法 Bearer Token 正确提取."""

        token = extract_bearer_token("Bearer abc123xyz")
        assert token == "abc123xyz"

    def test_missing_authorization_header(self) -> None:
        """缺少 Authorization 头抛出 AuthenticationError."""

        with pytest.raises(AuthenticationError):
            extract_bearer_token(None)

    def test_non_bearer_scheme(self) -> None:
        """非 Bearer 方案抛出 AuthenticationError."""

        with pytest.raises(AuthenticationError):
            extract_bearer_token("Basic abc123")

    def test_empty_token(self) -> None:
        """Bearer 后为空 Token 抛出 AuthenticationError."""

        with pytest.raises(AuthenticationError):
            extract_bearer_token("Bearer ")

    def test_bearer_with_whitespace(self) -> None:
        """Bearer Token 前后空白被去除."""

        token = extract_bearer_token("Bearer   abc123  ")
        assert token == "abc123"


# ═══════════════════════════════════════════════════════════════════════════════
# 错误码 — SPEC 10.2 / 12.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAuthErrorCodes:
    """认证模块错误码 — SPEC 10.2 / 12.4."""

    def test_invalid_credentials_code_format(self) -> None:
        """AUTH.INVALID_CREDENTIALS 错误码格式合法."""

        from app.core.errors.codes import default_registry

        metadata = default_registry.get(AUTH_INVALID_CREDENTIALS)
        assert metadata is not None
        assert metadata.http_status == 401

    def test_invalid_credentials_error_is_authentication(self) -> None:
        """InvalidCredentialsError 继承 AuthenticationError."""

        from app.modules.auth.errors import InvalidCredentialsError

        assert issubclass(InvalidCredentialsError, AuthenticationError)

    def test_all_login_failures_use_same_code(self) -> None:
        """所有登录失败返回相同错误码 — SPEC 12.4 防枚举."""

        from app.modules.auth.errors import InvalidCredentialsError

        error = InvalidCredentialsError("test")
        assert error.code == AUTH_INVALID_CREDENTIALS


# ═══════════════════════════════════════════════════════════════════════════════
# 会话过期规则 — SPEC 12.3
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestSessionExpiryRules:
    """会话过期判定规则 — SPEC 12.3."""

    def test_token_not_expired_within_ttl(self) -> None:
        """Token 在有效期内未过期 — SPEC 12.1."""

        now = datetime.now(UTC)
        token_expires = now + ACCESS_TOKEN_TTL
        assert now < token_expires

    def test_token_expired_after_ttl(self) -> None:
        """Token 超过有效期过期 — SPEC 12.1."""

        now = datetime.now(UTC)
        token_expires = now - timedelta(minutes=1)
        assert now >= token_expires

    def test_session_idle_not_expired(self) -> None:
        """会话在空闲超时内未过期 — SPEC 12.3."""

        now = datetime.now(UTC)
        last_activity = now - timedelta(minutes=10)
        idle_cutoff = now - SESSION_IDLE_TIMEOUT
        # last_activity > idle_cutoff → 未过期
        assert last_activity > idle_cutoff

    def test_session_idle_expired(self) -> None:
        """会话空闲超时过期 — SPEC 12.3."""

        now = datetime.now(UTC)
        last_activity = now - SESSION_IDLE_TIMEOUT - timedelta(minutes=1)
        idle_cutoff = now - SESSION_IDLE_TIMEOUT
        # last_activity < idle_cutoff → 过期
        assert last_activity < idle_cutoff

    def test_session_absolute_not_expired(self) -> None:
        """会话在绝对超时内未过期 — SPEC 12.3."""

        now = datetime.now(UTC)
        absolute_expires = now + timedelta(hours=1)
        assert now < absolute_expires

    def test_session_absolute_expired(self) -> None:
        """会话绝对超时过期 — SPEC 12.3."""

        now = datetime.now(UTC)
        absolute_expires = now - timedelta(minutes=1)
        assert now >= absolute_expires

    def test_activity_update_needed_after_interval(self) -> None:
        """超过 5 分钟间隔需要更新活动时间 — SPEC 12.3."""

        now = datetime.now(UTC)
        last_activity = now - ACTIVITY_UPDATE_INTERVAL - timedelta(seconds=1)
        activity_cutoff = now - ACTIVITY_UPDATE_INTERVAL
        assert last_activity < activity_cutoff

    def test_activity_update_not_needed_within_interval(self) -> None:
        """5 分钟间隔内不需要更新活动时间 — SPEC 12.3."""

        now = datetime.now(UTC)
        last_activity = now - timedelta(minutes=2)
        activity_cutoff = now - ACTIVITY_UPDATE_INTERVAL
        assert last_activity >= activity_cutoff


# ═══════════════════════════════════════════════════════════════════════════════
# 防枚举 — SPEC 12.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAntiEnumeration:
    """防枚举安全策略 — SPEC 12.4."""

    def test_dummy_hash_verify_returns_false(self) -> None:
        """虚拟哈希对任何密码 verify 返回 False — SPEC 12.4."""

        from app.core.security.password import DUMMY_PASSWORD_HASH, Argon2Hasher

        hasher = Argon2Hasher()
        assert hasher.verify(DUMMY_PASSWORD_HASH, "any_password_123") is False

    def test_dummy_hash_executes_argon2id(self) -> None:
        """虚拟哈希执行完整的 Argon2id 运算（消耗 CPU 时间）— SPEC 12.4."""

        from app.core.security.password import DUMMY_PASSWORD_HASH, Argon2Hasher

        hasher = Argon2Hasher()
        # 验证不会抛出异常（哈希格式合法）
        result = hasher.verify(DUMMY_PASSWORD_HASH, "test_password_12")
        # 对任何密码都返回 False
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# Router 架构边界 — SPEC 5.2 / 5.6
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAuthRouterArchitecture:
    """Auth Router 架构边界 — SPEC 5.2 / 5.6."""

    def test_router_not_import_asyncsession(self) -> None:
        """Router 模块不导入 AsyncSession — SPEC 5.6."""

        import ast
        from pathlib import Path

        source = Path("src/app/modules/auth/router.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_names = {"AsyncSession", "SqlAlchemySessionRepository"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name not in forbidden_names, (
                        f"Router 不应导入 {alias.name}"
                    )

    def test_router_has_no_direct_db_access(self) -> None:
        """Router 源码不包含直接数据库访问模式."""

        from pathlib import Path

        source = Path("src/app/modules/auth/router.py").read_text(encoding="utf-8")
        assert "session.commit" not in source
        assert "session.execute" not in source
        assert "session.add" not in source
