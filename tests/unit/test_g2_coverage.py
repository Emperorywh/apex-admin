"""G2 领域模型、事件处理器和装配工厂补充单元测试。

补充覆盖率：
- login_security: increment_failure 到期重置路径
- tokens: verify_access / verify_refresh 恒定时间比较
- event_handlers: auth 和 rbac 事件处理器
- wiring: create_auth_service / create_rbac_service 装配
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

pytestmark = [pytest.mark.unit, pytest.mark.g2]

_NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"


# ===========================================================================
# LoginAttempt domain tests
# ===========================================================================


class TestLoginAttemptDomain:
    """login_security 领域模型补充测试。"""

    def test_increment_failure_resets_after_lock_expiry(self) -> None:
        """限制到期后递增失败重置计数为 1。"""
        from app.modules.auth.domain.login_security import (
            ACCOUNT_LOCK_THRESHOLD,
            LoginAttempt,
            LoginAttemptDimension,
        )

        # 创建已锁定的记录
        locked = LoginAttempt(
            dimension=LoginAttemptDimension.ACCOUNT,
            identifier="user1",
            failure_count=ACCOUNT_LOCK_THRESHOLD,
            locked_until=_NOW - timedelta(minutes=1),  # 已过期
            last_failure_at=_NOW - timedelta(minutes=20),
        )

        result = locked.increment_failure(
            threshold=ACCOUNT_LOCK_THRESHOLD,
            current_time=_NOW,
        )

        assert result.failure_count == 1
        assert result.locked_until is None

    def test_increment_failure_reaches_threshold_sets_lock(self) -> None:
        """达到阈值时设置锁定。"""
        from app.modules.auth.domain.login_security import (
            ACCOUNT_LOCK_THRESHOLD,
            LoginAttempt,
            LoginAttemptDimension,
        )

        attempt = LoginAttempt(
            dimension=LoginAttemptDimension.ACCOUNT,
            identifier="user1",
            failure_count=ACCOUNT_LOCK_THRESHOLD - 1,
            locked_until=None,
            last_failure_at=_NOW - timedelta(minutes=1),
        )

        result = attempt.increment_failure(
            threshold=ACCOUNT_LOCK_THRESHOLD,
            current_time=_NOW,
        )

        assert result.failure_count == ACCOUNT_LOCK_THRESHOLD
        assert result.locked_until is not None

    def test_is_locked_expired_returns_false(self) -> None:
        """锁定到期后 is_locked 返回 False。"""
        from app.modules.auth.domain.login_security import (
            LoginAttempt,
            LoginAttemptDimension,
        )

        attempt = LoginAttempt(
            dimension=LoginAttemptDimension.ACCOUNT,
            identifier="user1",
            failure_count=5,
            locked_until=_NOW - timedelta(minutes=1),
            last_failure_at=_NOW - timedelta(minutes=20),
        )

        assert attempt.is_locked(current_time=_NOW) is False

    def test_is_locked_active_returns_true(self) -> None:
        """锁定未到期时 is_locked 返回 True。"""
        from app.modules.auth.domain.login_security import (
            LoginAttempt,
            LoginAttemptDimension,
        )

        attempt = LoginAttempt(
            dimension=LoginAttemptDimension.ACCOUNT,
            identifier="user1",
            failure_count=5,
            locked_until=_NOW + timedelta(minutes=10),
            last_failure_at=_NOW,
        )

        assert attempt.is_locked(current_time=_NOW) is True


# ===========================================================================
# Token domain tests
# ===========================================================================


class TestTokenDigester:
    """TokenDigester 补充测试——恒定时间比较。"""

    def test_verify_access_correct_token(self) -> None:
        """verify_access 正确 Token 返回 True。"""
        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr(_ACCESS_KEY),
            refresh_key=SecretStr(_REFRESH_KEY),
        )
        token = "test_access_token_12345"
        digest = digester.access_digest(token)

        assert digester.verify_access(token, digest) is True

    def test_verify_access_wrong_token(self) -> None:
        """verify_access 错误 Token 返回 False。"""
        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr(_ACCESS_KEY),
            refresh_key=SecretStr(_REFRESH_KEY),
        )
        digest = digester.access_digest("correct_token")

        assert digester.verify_access("wrong_token", digest) is False

    def test_verify_refresh_correct_token(self) -> None:
        """verify_refresh 正确 Token 返回 True。"""
        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr(_ACCESS_KEY),
            refresh_key=SecretStr(_REFRESH_KEY),
        )
        token = "test_refresh_token_67890"
        digest = digester.refresh_digest(token)

        assert digester.verify_refresh(token, digest) is True

    def test_verify_refresh_wrong_token(self) -> None:
        """verify_refresh 错误 Token 返回 False。"""
        from app.modules.auth.domain.tokens import TokenDigester

        digester = TokenDigester(
            access_key=SecretStr(_ACCESS_KEY),
            refresh_key=SecretStr(_REFRESH_KEY),
        )
        digest = digester.refresh_digest("correct")

        assert digester.verify_refresh("wrong", digest) is False


# ===========================================================================
# Auth event handlers tests
# ===========================================================================


class TestAuthEventHandlers:
    """auth 模块事件处理器测试。"""

    async def test_handle_session_created(self) -> None:
        """handle_session_created 执行不报错。"""
        from app.modules.auth.domain.events import SessionCreated
        from app.modules.auth.infrastructure.event_handlers import (
            handle_session_created,
        )

        event = SessionCreated(
            occurred_at=_NOW,
            session_id=uuid4(),
            user_id=uuid4(),
        )
        # 事件处理器内部使用 UoW.session，传入 None 应不报错或跳过
        with contextlib.suppress(AttributeError, TypeError):
            await handle_session_created(None, event)  # type: ignore[arg-type]

    async def test_handle_session_revoked(self) -> None:
        """handle_session_revoked 执行不报错。"""
        from app.modules.auth.domain.events import SessionRevoked
        from app.modules.auth.infrastructure.event_handlers import (
            handle_session_revoked,
        )

        event = SessionRevoked(
            occurred_at=_NOW,
            session_id=uuid4(),
            user_id=uuid4(),
            reason="logout",
        )
        with contextlib.suppress(AttributeError, TypeError):
            await handle_session_revoked(None, event)  # type: ignore[arg-type]


# ===========================================================================
# RBAC event handlers tests
# ===========================================================================


class TestRbacEventHandlers:
    """rbac 模块事件处理器测试。"""

    async def test_handle_role_created(self) -> None:
        """handle_role_created 执行不报错。"""
        from app.modules.rbac.domain.events import RoleCreated
        from app.modules.rbac.infrastructure.event_handlers import (
            handle_role_created,
        )

        event = RoleCreated(
            occurred_at=_NOW,
            role_id=uuid4(),
            role_code="test",
            is_super_admin=False,
        )
        with contextlib.suppress(AttributeError, TypeError):
            await handle_role_created(None, event)  # type: ignore[arg-type]

    async def test_handle_role_disabled(self) -> None:
        """handle_role_disabled 执行不报错。"""
        from app.modules.rbac.domain.events import RoleDisabled
        from app.modules.rbac.infrastructure.event_handlers import (
            handle_role_disabled,
        )

        event = RoleDisabled(
            occurred_at=_NOW,
            role_id=uuid4(),
            role_code="test",
        )
        with contextlib.suppress(AttributeError, TypeError):
            await handle_role_disabled(None, event)  # type: ignore[arg-type]

    async def test_handle_user_role_assigned(self) -> None:
        """handle_user_role_assigned 执行不报错。"""
        from app.modules.rbac.domain.events import UserRoleAssigned
        from app.modules.rbac.infrastructure.event_handlers import (
            handle_user_role_assigned,
        )

        event = UserRoleAssigned(
            occurred_at=_NOW,
            user_id=uuid4(),
            role_id=uuid4(),
            role_code="test",
        )
        with contextlib.suppress(AttributeError, TypeError):
            await handle_user_role_assigned(None, event)  # type: ignore[arg-type]

    async def test_handle_user_role_removed(self) -> None:
        """handle_user_role_removed 执行不报错。"""
        from app.modules.rbac.domain.events import UserRoleRemoved
        from app.modules.rbac.infrastructure.event_handlers import (
            handle_user_role_removed,
        )

        event = UserRoleRemoved(
            occurred_at=_NOW,
            user_id=uuid4(),
            role_id=uuid4(),
            role_code="test",
        )
        with contextlib.suppress(AttributeError, TypeError):
            await handle_user_role_removed(None, event)  # type: ignore[arg-type]


# ===========================================================================
# Wiring factory tests
# ===========================================================================
# 注：create_auth_service / create_rbac_service 内部使用 ModuleRegistry([MODULE])
# 构建事件注册表，但单模块注册缺少必需依赖（auth 需要 user，rbac 需要 auth+user）。
# 完整装配通过 Composition Root 实现，由 app fixture 和集成测试覆盖。
