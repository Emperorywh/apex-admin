"""用户模块单元测试 — SPEC 11.1 / 11.2 / 11.3 / 18.2 / 5.7.

覆盖:
  - 领域实体、状态枚举与 Schema 验证（不连接数据库）。
  - 错误码注册与异常类型。
  - 事件载荷只含标量值。
  - 自助端点 Schema 白名单（extra="forbid"）。
  - 自助改密 Schema 包含 old_password。
  - 审计字段白名单拒绝 password_hash。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.security.password import PASSWORD_MAX_LENGTH
from app.modules.identity.errors import (
    USER_ALREADY_ACTIVE,
    USER_ALREADY_DISABLED,
    USER_ALREADY_EXISTS,
    USER_HAS_AUDIT_RECORDS,
    USER_INVALID_OLD_PASSWORD,
    USER_NOT_FOUND,
    UserAlreadyActiveError,
    UserAlreadyDisabledError,
    UserAlreadyExistsError,
    UserHasAuditRecordsError,
    UserInvalidOldPasswordError,
    UserNotFoundError,
)
from app.modules.identity.events import PasswordResetByAdmin, UserDisabled
from app.modules.identity.models import User, UserStatus

# ═══════════════════════════════════════════════════════════════════════════════
# 领域实体与状态枚举
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestUserStatus:
    """用户状态枚举 — SPEC 8.3 / 11.2."""

    def test_active_value(self) -> None:
        """ACTIVE 状态稳定编码为 'active'。"""
        assert UserStatus.ACTIVE.value == "active"

    def test_disabled_value(self) -> None:
        """DISABLED 状态稳定编码为 'disabled'。"""
        assert UserStatus.DISABLED.value == "disabled"

    def test_from_string(self) -> None:
        """从字符串构造状态枚举 — 稳定编码双向映射。"""
        assert UserStatus("active") == UserStatus.ACTIVE
        assert UserStatus("disabled") == UserStatus.DISABLED

    def test_invalid_status_raises(self) -> None:
        """非法状态字符串构造失败。"""
        with pytest.raises(ValueError):
            UserStatus("invalid")


@pytest.mark.g2
@pytest.mark.unit
class TestUserEntity:
    """用户领域实体 — SPEC 11.2."""

    def test_user_is_frozen(self) -> None:
        """用户实体不可变 — SPEC 5.2: frozen dataclass。"""
        user = _make_user()
        with pytest.raises(AttributeError):
            user.display_name = "changed"  # type: ignore[misc]

    def test_user_fields(self) -> None:
        """用户实体包含 SPEC 11.2 所有字段。"""
        user = _make_user()
        assert user.username == "alice"
        assert user.display_name == "Alice"
        assert user.status == UserStatus.ACTIVE
        assert user.password_hash == "$argon2id$fake"
        assert user.phone is None
        assert user.email is None
        assert user.last_login_at is None
        assert user.password_updated_at is not None
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_password_hash_not_in_response_model(self) -> None:
        """响应模型不包含 password_hash — SPEC 9.3 / 23.2."""
        from app.modules.identity.schemas import UserResponse

        field_names = set(UserResponse.model_fields.keys())
        assert "password_hash" not in field_names
        assert "password" not in field_names


# ═══════════════════════════════════════════════════════════════════════════════
# Schema 验证
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestUserCreateRequest:
    """创建用户请求 Schema — SPEC 9.2 / 23.2."""

    def test_valid_create(self) -> None:
        """合法创建请求。"""
        from app.modules.identity.schemas import UserCreateRequest

        req = UserCreateRequest(
            username="alice",
            display_name="Alice",
            password="secure_password_12",
        )
        assert req.username == "alice"

    def test_short_password_rejected(self) -> None:
        """密码不足 12 字符被拒绝 — SPEC 23.2."""
        from app.modules.identity.schemas import UserCreateRequest

        with pytest.raises(ValidationError):
            UserCreateRequest(
                username="alice",
                display_name="Alice",
                password="short",
            )

    def test_long_password_rejected(self) -> None:
        """密码超过 128 字符被拒绝 — SPEC 23.2."""
        from app.modules.identity.schemas import UserCreateRequest

        with pytest.raises(ValidationError):
            UserCreateRequest(
                username="alice",
                display_name="Alice",
                password="a" * (PASSWORD_MAX_LENGTH + 1),
            )

    def test_unknown_field_rejected(self) -> None:
        """未知字段返回 422 — SPEC 9.2: extra="forbid"."""
        from app.modules.identity.schemas import UserCreateRequest

        with pytest.raises(ValidationError):
            UserCreateRequest(
                username="alice",
                display_name="Alice",
                password="secure_password_12",
                unknown_field="value",
            )

    def test_short_username_rejected(self) -> None:
        """用户名不足 3 字符被拒绝。"""
        from app.modules.identity.schemas import UserCreateRequest

        with pytest.raises(ValidationError):
            UserCreateRequest(
                username="ab",
                display_name="Alice",
                password="secure_password_12",
            )


@pytest.mark.g2
@pytest.mark.unit
class TestSelfProfileUpdateRequest:
    """自助更新资料请求 Schema — SPEC 11.1 白名单字段.

    SPEC 11.1: 自助端点仅允许白名单字段（display_name、phone、email）。
    """

    def test_valid_self_update(self) -> None:
        """合法自助更新请求。"""
        from app.modules.identity.schemas import SelfProfileUpdateRequest

        req = SelfProfileUpdateRequest(
            display_name="New Name",
            phone="13800138000",
            email="alice@example.com",
        )
        assert req.display_name == "New Name"

    def test_username_field_rejected(self) -> None:
        """自助更新不允许 username — 白名单校验（SPEC 11.1）."""
        from app.modules.identity.schemas import SelfProfileUpdateRequest

        with pytest.raises(ValidationError):
            SelfProfileUpdateRequest(  # type: ignore[call-arg]
                display_name="Alice",
                username="hacker",
            )

    def test_status_field_rejected(self) -> None:
        """自助更新不允许 status — 白名单校验（SPEC 11.1）."""
        from app.modules.identity.schemas import SelfProfileUpdateRequest

        with pytest.raises(ValidationError):
            SelfProfileUpdateRequest(  # type: ignore[call-arg]
                display_name="Alice",
                status="disabled",
            )

    def test_password_field_rejected(self) -> None:
        """自助资料更新不允许 password — 白名单校验."""
        from app.modules.identity.schemas import SelfProfileUpdateRequest

        with pytest.raises(ValidationError):
            SelfProfileUpdateRequest(  # type: ignore[call-arg]
                display_name="Alice",
                password="new_password_123",
            )


@pytest.mark.g2
@pytest.mark.unit
class TestSelfChangePasswordRequest:
    """自助改密请求 Schema — SPEC 11.1.

    SPEC 11.1: 自助改密必须校验旧密码。
    """

    def test_valid_change_password(self) -> None:
        """合法改密请求包含 old_password 和 new_password。"""
        from app.modules.identity.schemas import SelfChangePasswordRequest

        req = SelfChangePasswordRequest(
            old_password="old_password_12",
            new_password="new_password_12",
        )
        assert req.old_password == "old_password_12"
        assert req.new_password == "new_password_12"

    def test_old_password_required(self) -> None:
        """old_password 为必填——自助改密必须校验旧密码（SPEC 11.1）。"""
        from app.modules.identity.schemas import SelfChangePasswordRequest

        with pytest.raises(ValidationError):
            SelfChangePasswordRequest(  # type: ignore[call-arg]
                new_password="new_password_12",
            )

    def test_new_password_min_length(self) -> None:
        """新密码满足最小长度要求（SPEC 23.2）。"""
        from app.modules.identity.schemas import SelfChangePasswordRequest

        with pytest.raises(ValidationError):
            SelfChangePasswordRequest(
                old_password="old_password_12",
                new_password="short",
            )

    def test_unknown_field_rejected(self) -> None:
        """未知字段被拒绝 — SPEC 9.2: extra="forbid"."""
        from app.modules.identity.schemas import SelfChangePasswordRequest

        with pytest.raises(ValidationError):
            SelfChangePasswordRequest(
                old_password="old_password_12",
                new_password="new_password_12",
                extra_field="value",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 错误码与异常
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestErrorCodes:
    """错误码格式与注册 — SPEC 10.2 / 5.5."""

    def test_not_found_code(self) -> None:
        """USER.NOT_FOUND 错误码与异常。"""
        assert USER_NOT_FOUND == "USER.NOT_FOUND"
        exc = UserNotFoundError("user-123")
        assert exc.code == USER_NOT_FOUND

    def test_already_exists_code(self) -> None:
        """USER.ALREADY_EXISTS 稳定冲突错误码 — SPEC 8.4。"""
        assert USER_ALREADY_EXISTS == "USER.ALREADY_EXISTS"
        exc = UserAlreadyExistsError("alice")
        assert exc.code == USER_ALREADY_EXISTS

    def test_already_disabled_code(self) -> None:
        """USER.ALREADY_DISABLED 错误码。"""
        assert USER_ALREADY_DISABLED == "USER.ALREADY_DISABLED"
        exc = UserAlreadyDisabledError("user-123")
        assert exc.code == USER_ALREADY_DISABLED

    def test_already_active_code(self) -> None:
        """USER.ALREADY_ACTIVE 错误码。"""
        assert USER_ALREADY_ACTIVE == "USER.ALREADY_ACTIVE"
        exc = UserAlreadyActiveError("user-123")
        assert exc.code == USER_ALREADY_ACTIVE

    def test_invalid_old_password_code(self) -> None:
        """USER.INVALID_OLD_PASSWORD 错误码。"""
        assert USER_INVALID_OLD_PASSWORD == "USER.INVALID_OLD_PASSWORD"
        exc = UserInvalidOldPasswordError("user-123")
        assert exc.code == USER_INVALID_OLD_PASSWORD

    def test_has_audit_records_code(self) -> None:
        """USER.HAS_AUDIT_RECORDS 错误码 — SPEC 11.3。"""
        assert USER_HAS_AUDIT_RECORDS == "USER.HAS_AUDIT_RECORDS"
        exc = UserHasAuditRecordsError("user-123")
        assert exc.code == USER_HAS_AUDIT_RECORDS

    def test_error_codes_registered(self) -> None:
        """所有错误码已注册到框架注册表。"""
        from app.core.errors.codes import default_registry

        for code in (
            USER_NOT_FOUND,
            USER_ALREADY_EXISTS,
            USER_ALREADY_DISABLED,
            USER_ALREADY_ACTIVE,
            USER_INVALID_OLD_PASSWORD,
            USER_HAS_AUDIT_RECORDS,
        ):
            assert code in default_registry, f"错误码 {code} 未注册"


# ═══════════════════════════════════════════════════════════════════════════════
# 事件
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestDomainEvents:
    """领域事件 — SPEC 5.7."""

    def test_user_disabled_event(self) -> None:
        """USER.DISABLED 事件载荷只含标量值。"""
        uid = str(uuid4())
        event = UserDisabled(
            code="USER.DISABLED",
            payload={"user_id": uid, "user_status": "disabled"},
            user_id=uid,
            user_status="disabled",
        )
        assert event.code == "USER.DISABLED"
        assert event.user_id == uid
        assert event.user_status == "disabled"
        # payload 只含标量值
        assert all(isinstance(v, str) for v in event.payload.values())

    def test_password_reset_event(self) -> None:
        """USER.PASSWORD_RESET_BY_ADMIN 事件载荷只含 user_id。"""
        uid = str(uuid4())
        event = PasswordResetByAdmin(
            code="USER.PASSWORD_RESET_BY_ADMIN",
            payload={"user_id": uid},
            user_id=uid,
        )
        assert event.code == "USER.PASSWORD_RESET_BY_ADMIN"
        assert event.user_id == uid
        # 载荷不含密码或哈希
        assert "password" not in str(event.payload).lower()
        assert "hash" not in str(event.payload).lower()

    def test_events_are_frozen(self) -> None:
        """事件是不可变对象 — SPEC 5.7。"""
        event = UserDisabled(code="USER.DISABLED")
        with pytest.raises(AttributeError):
            event.user_id = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# 审计字段白名单
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAuditWhitelist:
    """用户审计字段白名单 — SPEC 18.2."""

    def test_whitelist_excludes_password(self) -> None:
        """审计白名单不包含 password_hash — SPEC 18.2."""
        from app.modules.identity.use_case import USER_FIELD_WHITELIST

        assert "password_hash" not in USER_FIELD_WHITELIST.fields
        assert "password" not in USER_FIELD_WHITELIST.fields

    def test_whitelist_includes_safe_fields(self) -> None:
        """审计白名单包含安全字段。"""
        from app.modules.identity.use_case import USER_FIELD_WHITELIST

        assert "display_name" in USER_FIELD_WHITELIST.fields
        assert "status" in USER_FIELD_WHITELIST.fields
        assert "phone" in USER_FIELD_WHITELIST.fields
        assert "email" in USER_FIELD_WHITELIST.fields


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════════════


def _make_user(
    *,
    status: UserStatus = UserStatus.ACTIVE,
    user_id: UUID | None = None,
) -> User:
    """构造测试用用户实体。"""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return User(
        id=user_id or uuid4(),
        username="alice",
        display_name="Alice",
        password_hash="$argon2id$fake",
        status=status,
        phone=None,
        email=None,
        last_login_at=None,
        password_updated_at=now,
        created_at=now,
        updated_at=now,
        created_by=None,
        updated_by=None,
    )
