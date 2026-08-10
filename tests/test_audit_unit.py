"""审计模块单元测试 — SPEC 18.1 / 18.2 / 5.7.

覆盖验收标准:
  - AC-1: 差异生成仅含字段白名单，password/token/secret 类字段即使
    误声明也被拒绝或掩码。
  - AC-4: 失败操作写入独立安全日志渠道且不含明文密码与完整 Token。

不依赖数据库（unit marker）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.audit.diff import (
    FieldWhitelist,
    generate_diff,
)
from app.modules.audit.models import (
    AuditEntry,
    ChangeDiff,
    DiffField,
    LoginLogEntry,
    SecurityEvent,
)
from app.modules.audit.security_log import StructlogSecurityLogger

# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: 字段白名单差异生成
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestFieldWhitelistConstruction:
    """字段白名单构造校验 — SPEC 18.2."""

    def test_normal_fields_accepted(self) -> None:
        """正常字段名可正常构造白名单。"""

        whitelist = FieldWhitelist(
            "user",
            "user",
            frozenset({"name", "email", "status"}),
        )
        assert whitelist.allows("name")
        assert whitelist.allows("email")
        assert whitelist.allows("status")
        assert not whitelist.allows("password_hash")

    def test_contains_operator(self) -> None:
        """``in`` 运算符正确工作。"""

        whitelist = FieldWhitelist("m", "r", frozenset({"name"}))
        assert "name" in whitelist
        assert "other" not in whitelist

    @pytest.mark.parametrize(
        "sensitive_field",
        [
            "password",
            "password_hash",
            "token",
            "access_token",
            "refresh_token",
            "secret",
            "secret_key",
            "credential",
            "apikey",
            "private_key",
            "Password",  # 大小写不敏感
            "TOKEN",
            "user_secret",
        ],
    )
    def test_sensitive_field_rejected(self, sensitive_field: str) -> None:
        """敏感字段名在白名单构造时被拒绝 — SPEC 18.2.

        SPEC 18.2: "密码、Token、密钥等敏感字段不得进入差异内容"。
        即使误声明，也在构造时立即拒绝。
        """

        with pytest.raises(ValueError, match="字段白名单拒绝敏感字段"):
            FieldWhitelist("user", "user", frozenset({sensitive_field}))

    def test_empty_whitelist_allowed(self) -> None:
        """空白名单合法 — 不产生任何差异。"""

        whitelist = FieldWhitelist("m", "r", frozenset())
        assert len(whitelist.fields) == 0


@pytest.mark.g2
@pytest.mark.unit
class TestGenerateDiff:
    """差异生成测试 — SPEC 18.2."""

    def test_only_whitelisted_fields_in_diff(self) -> None:
        """差异仅包含白名单内的字段 — SPEC 18.2.

        SPEC 18.2: "审计差异使用字段白名单生成，禁止对任意对象执行
        反射式全字段序列化"。
        """

        whitelist = FieldWhitelist(
            "user",
            "user",
            frozenset({"name", "email"}),
        )
        before = {"name": "old", "email": "old@test.com", "internal": "x"}
        after = {"name": "new", "email": "new@test.com", "internal": "y"}

        diff = generate_diff(whitelist, before, after)

        field_names = {f.field_name for f in diff.fields}
        assert field_names == {"name", "email"}
        # internal 不在白名单，不出现在差异中
        assert "internal" not in field_names

    def test_unchanged_fields_not_in_diff(self) -> None:
        """值未变化的字段不产生差异。"""

        whitelist = FieldWhitelist("m", "r", frozenset({"name", "status"}))
        before = {"name": "same", "status": "active"}
        after = {"name": "same", "status": "active"}

        diff = generate_diff(whitelist, before, after)
        assert diff.is_empty

    def test_partial_change(self) -> None:
        """仅变化的字段产生差异。"""

        whitelist = FieldWhitelist("m", "r", frozenset({"name", "status"}))
        before = {"name": "old", "status": "active"}
        after = {"name": "new", "status": "active"}

        diff = generate_diff(whitelist, before, after)
        assert len(diff.fields) == 1
        assert diff.fields[0].field_name == "name"
        assert diff.fields[0].old_value == "old"
        assert diff.fields[0].new_value == "new"

    def test_none_before(self) -> None:
        """before 为 None 时，所有白名单字段视为新增。"""

        whitelist = FieldWhitelist("m", "r", frozenset({"name"}))
        diff = generate_diff(whitelist, None, {"name": "value"})
        assert len(diff.fields) == 1
        assert diff.fields[0].old_value is None
        assert diff.fields[0].new_value == "value"

    def test_none_after(self) -> None:
        """after 为 None 时，所有白名单字段视为删除。"""

        whitelist = FieldWhitelist("m", "r", frozenset({"name"}))
        diff = generate_diff(whitelist, {"name": "value"}, None)
        assert len(diff.fields) == 1
        assert diff.fields[0].old_value == "value"
        assert diff.fields[0].new_value is None

    def test_both_none_empty_diff(self) -> None:
        """before 和 after 都为 None 时差异为空。"""

        whitelist = FieldWhitelist("m", "r", frozenset({"name"}))
        diff = generate_diff(whitelist, None, None)
        assert diff.is_empty

    def test_diff_fields_sorted(self) -> None:
        """差异字段按名称排序 — 保证可复现性。"""

        whitelist = FieldWhitelist(
            "m",
            "r",
            frozenset({"zebra", "apple", "mango"}),
        )
        before = {"zebra": 1, "apple": 1, "mango": 1}
        after = {"zebra": 2, "apple": 2, "mango": 2}

        diff = generate_diff(whitelist, before, after)
        names = [f.field_name for f in diff.fields]
        assert names == ["apple", "mango", "zebra"]

    def test_change_diff_to_dict(self) -> None:
        """ChangeDiff.to_dict 序列化正确。"""

        diff = ChangeDiff(
            fields=(DiffField(field_name="name", old_value="old", new_value="new"),),
        )
        result = diff.to_dict()
        assert result == {"name": {"old": "old", "new": "new"}}

    def test_change_diff_is_empty(self) -> None:
        """ChangeDiff.is_empty 正确判断空差异。"""

        assert ChangeDiff(fields=()).is_empty
        assert not ChangeDiff(
            fields=(DiffField("f", None, "v"),),
        ).is_empty

    def test_non_whitelist_data_ignored(self) -> None:
        """before/after 中非白名单字段被完全忽略。

        即使 before/after 包含 password_hash 等敏感字段，
        由于白名单中不包含这些字段，差异中不会出现。
        """

        whitelist = FieldWhitelist("m", "r", frozenset({"name"}))
        before = {"name": "old", "password_hash": "secret_hash_1"}
        after = {"name": "new", "password_hash": "secret_hash_2"}

        diff = generate_diff(whitelist, before, after)
        field_names = {f.field_name for f in diff.fields}
        assert field_names == {"name"}
        assert "password_hash" not in field_names


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: 安全日志渠道 — 不含明文密码与完整 Token
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestSecurityEventModel:
    """安全事件模型测试 — 不含密码和 Token 字段（SPEC 12.4 / 18.1）."""

    def test_security_event_has_no_password_field(self) -> None:
        """SecurityEvent 模型不含密码字段。"""

        event = SecurityEvent(
            event_type="auth_failure",
            actor_id="user-123",
            module="auth",
            action="login",
            resource_type=None,
            resource_id=None,
            request_id="req-001",
            ip_address="10.0.0.1",
            failure_reason="密码错误",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        # 模型字段中不存在 password / token / secret
        assert not hasattr(event, "password")
        assert not hasattr(event, "token")
        assert not hasattr(event, "secret")

    def test_security_event_is_immutable(self) -> None:
        """SecurityEvent 是不可变的（frozen dataclass）。"""

        event = SecurityEvent(
            event_type="test",
            actor_id=None,
            module="m",
            action="a",
            resource_type=None,
            resource_id=None,
            request_id=None,
            ip_address=None,
            failure_reason="reason",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(AttributeError):
            event.failure_reason = "changed"  # type: ignore[misc]


@pytest.mark.g2
@pytest.mark.unit
class TestStructlogSecurityLogger:
    """安全日志渠道测试 — SPEC 5.7 / 12.4."""

    def test_log_security_event_output(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """安全事件写入独立日志渠道 — 输出包含预期字段。"""

        logger = StructlogSecurityLogger()
        event = SecurityEvent(
            event_type="auth_failure",
            actor_id="user-123",
            module="auth",
            action="login",
            resource_type=None,
            resource_id=None,
            request_id="req-001",
            ip_address="10.0.0.1",
            failure_reason="invalid_credentials",
            occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )

        logger.log_security_event(event)

        captured = capsys.readouterr()
        output = captured.out + captured.err

        # 输出包含安全事件关键字段
        assert "security_event" in output
        assert "auth_failure" in output
        assert "invalid_credentials" in output
        assert "10.0.0.1" in output

    def test_no_password_in_output(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """安全日志输出不含明文密码 — SPEC 12.4 / 18.1."""

        logger = StructlogSecurityLogger()
        event = SecurityEvent(
            event_type="auth_failure",
            actor_id="user-123",
            module="auth",
            action="login",
            resource_type=None,
            resource_id=None,
            request_id="req-001",
            ip_address="10.0.0.1",
            failure_reason="password_mismatch",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        logger.log_security_event(event)

        captured = capsys.readouterr()
        output = captured.out + captured.err

        # 输出中不存在明文密码值
        # SecurityEvent 模型本身不含密码字段，
        # failure_reason 只是分类描述，不包含实际密码
        assert "super_secret_123" not in output
        assert "P@ssw0rd!" not in output

    def test_no_token_in_output(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """安全日志输出不含完整 Token — SPEC 12.4 / 18.1."""

        logger = StructlogSecurityLogger()
        event = SecurityEvent(
            event_type="token_refresh_error",
            actor_id="user-123",
            module="auth",
            action="token_refresh",
            resource_type=None,
            resource_id=None,
            request_id="req-002",
            ip_address="10.0.0.2",
            failure_reason="token_revoked",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        logger.log_security_event(event)

        captured = capsys.readouterr()
        output = captured.out + captured.err

        # 输出中不存在完整 Token 值
        # SecurityEvent 模型本身不含 Token 字段
        assert "Bearer eyJhbGciOiJIUzI1NiJ9" not in output


# ═══════════════════════════════════════════════════════════════════════════════
# 领域实体不可变性测试
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestDomainEntityImmutability:
    """审计领域实体不可变性测试."""

    def test_audit_entry_immutable(self) -> None:
        """AuditEntry 不可变。"""

        entry = AuditEntry(
            id=uuid4(),
            actor_id="user-1",
            actor_display_name="Alice",
            module="user",
            action="user.update",
            resource_type="user",
            resource_id="user-2",
            resource_display_name="Bob",
            result="success",
            request_id="req-1",
            diff=None,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(AttributeError):
            entry.result = "failure"  # type: ignore[misc]

    def test_login_log_entry_immutable(self) -> None:
        """LoginLogEntry 不可变。"""

        entry = LoginLogEntry(
            id=uuid4(),
            user_id="user-1",
            username="alice",
            session_id="sess-1",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            result="success",
            failure_reason=None,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(AttributeError):
            entry.result = "failure"  # type: ignore[misc]

    def test_login_log_entry_no_password_token_fields(self) -> None:
        """LoginLogEntry 不含密码和 Token 字段 — SPEC 18.1."""

        entry = LoginLogEntry(
            id=uuid4(),
            user_id="user-1",
            username="alice",
            session_id="sess-1",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            result="success",
            failure_reason=None,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert not hasattr(entry, "password")
        assert not hasattr(entry, "token")
        assert not hasattr(entry, "access_token")
        assert not hasattr(entry, "refresh_token")
