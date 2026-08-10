"""Token HMAC 密钥轮换单元测试 — SPEC 23.2.

覆盖验收标准:
  - AC-3: Token HMAC 密钥轮换命令支持双密钥短期切换，
          旧密钥超窗后校验失败且不存在永久 fallback。

SPEC 23.2: "密钥轮换必须具有独立管理命令和双密钥短期切换步骤，
不得通过永久 fallback 兼容旧密钥"。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security.digest import (
    MIN_KEY_BYTES,
    TokenDigestService,
    TokenDigestValidationError,
)

# ── 测试密钥 ───────────────────────────────────────────────────────────────

_ACCESS_KEY = b"a" * MIN_KEY_BYTES
_REFRESH_KEY = b"b" * MIN_KEY_BYTES
_PREV_ACCESS_KEY = b"c" * MIN_KEY_BYTES
_PREV_REFRESH_KEY = b"d" * MIN_KEY_BYTES


# ── 轮换基础行为 ───────────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_no_rotation_returns_single_digest() -> None:
    """无前一代密钥时 candidate 只返回当前密钥摘要."""

    service = TokenDigestService(_ACCESS_KEY, _REFRESH_KEY)
    assert service.is_rotation_active() is False

    candidates = service.candidate_access_digests("test-token")
    assert len(candidates) == 1


@pytest.mark.g2
@pytest.mark.unit
def test_rotation_active_returns_dual_digests() -> None:
    """轮换窗口内返回当前和前一代两个摘要候选."""

    future = datetime.now(UTC) + timedelta(hours=1)
    service = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        previous_access_key=_PREV_ACCESS_KEY,
        previous_refresh_key=_PREV_REFRESH_KEY,
        rotation_expires_at=future,
    )
    assert service.is_rotation_active() is True

    access_candidates = service.candidate_access_digests("test-token")
    assert len(access_candidates) == 2
    # 当前密钥摘要始终在首位
    assert access_candidates[0] == service.digest_access_token("test-token")

    refresh_candidates = service.candidate_refresh_digests("test-token")
    assert len(refresh_candidates) == 2
    assert refresh_candidates[0] == service.digest_refresh_token("test-token")


@pytest.mark.g2
@pytest.mark.unit
def test_rotation_dual_digests_verify_old_token() -> None:
    """轮换窗口内前一代密钥可验证旧 Token."""

    future = datetime.now(UTC) + timedelta(hours=24)

    # 使用旧密钥创建的摘要（模拟轮换前已有的 Token）
    old_service = TokenDigestService(_PREV_ACCESS_KEY, _PREV_REFRESH_KEY)
    old_access_digest = old_service.digest_access_token("old-token")
    old_refresh_digest = old_service.digest_refresh_token("old-refresh")

    # 轮换后的服务
    new_service = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        previous_access_key=_PREV_ACCESS_KEY,
        previous_refresh_key=_PREV_REFRESH_KEY,
        rotation_expires_at=future,
    )

    # 旧摘要应在候选列表中
    assert old_access_digest in new_service.candidate_access_digests("old-token")
    assert old_refresh_digest in new_service.candidate_refresh_digests("old-refresh")

    # 新 Token 的摘要也应在候选列表中
    new_digest = new_service.digest_access_token("new-token")
    assert new_digest in new_service.candidate_access_digests("new-token")


# ── 超窗行为 ───────────────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_rotation_expired_returns_single_digest() -> None:
    """超窗后前一代密钥失效，只返回当前密钥摘要 — SPEC 23.2."""

    past = datetime.now(UTC) - timedelta(hours=1)
    service = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        previous_access_key=_PREV_ACCESS_KEY,
        previous_refresh_key=_PREV_REFRESH_KEY,
        rotation_expires_at=past,
    )
    assert service.is_rotation_active() is False

    candidates = service.candidate_access_digests("test-token")
    assert len(candidates) == 1
    assert candidates[0] == service.digest_access_token("test-token")


@pytest.mark.g2
@pytest.mark.unit
def test_rotation_expired_old_key_fails() -> None:
    """超窗后旧密钥的摘要不在候选列表中 — 无永久 fallback."""

    past = datetime.now(UTC) - timedelta(seconds=1)

    # 旧密钥产生的摘要
    old_service = TokenDigestService(_PREV_ACCESS_KEY, _PREV_REFRESH_KEY)
    old_digest = old_service.digest_access_token("old-token")

    # 超窗后的新服务
    expired_service = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        previous_access_key=_PREV_ACCESS_KEY,
        previous_refresh_key=_PREV_REFRESH_KEY,
        rotation_expires_at=past,
    )

    candidates = expired_service.candidate_access_digests("old-token")
    assert old_digest not in candidates
    assert len(candidates) == 1


# ── 时间注入测试 ───────────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_rotation_window_boundary_with_injected_time() -> None:
    """使用注入时间精确测试窗口边界.

    SPEC 23.2: 窗口过期是确定性时间点，非模糊判断。
    """

    expires_at = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)

    def time_before() -> datetime:
        return expires_at - timedelta(minutes=1)

    def time_at() -> datetime:
        return expires_at

    def time_after() -> datetime:
        return expires_at + timedelta(minutes=1)

    base_kwargs = {
        "previous_access_key": _PREV_ACCESS_KEY,
        "previous_refresh_key": _PREV_REFRESH_KEY,
        "rotation_expires_at": expires_at,
    }

    # 窗口前 — 活动状态
    svc_before = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        now_provider=time_before,
        **base_kwargs,
    )
    assert svc_before.is_rotation_active() is True
    assert len(svc_before.candidate_access_digests("token")) == 2

    # 窗口边界 — 仍然活动（now == expires_at 不算超时）
    svc_at = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        now_provider=time_at,
        **base_kwargs,
    )
    assert svc_at.is_rotation_active() is True

    # 窗口后 — 不活动
    svc_after = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        now_provider=time_after,
        **base_kwargs,
    )
    assert svc_after.is_rotation_active() is False
    assert len(svc_after.candidate_access_digests("token")) == 1


# ── 无永久 fallback ────────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_no_permanent_fallback_without_expiry() -> None:
    """无 rotation_expires_at 时不视为轮换活动（前一代密钥不生效）.

    SPEC 23.2: "不得通过永久 fallback 兼容旧密钥"。
    没有明确窗口时间的 previous key 不应永远有效。
    """

    service = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        previous_access_key=_PREV_ACCESS_KEY,
        previous_refresh_key=_PREV_REFRESH_KEY,
        rotation_expires_at=None,
    )
    # 无 expiration 时，前一代密钥不激活——防止永久 fallback
    assert service.is_rotation_active() is False
    candidates = service.candidate_access_digests("token")
    assert len(candidates) == 1  # 只有当前密钥摘要


# ── 前一代密钥校验 ─────────────────────────────────────────────────────────


@pytest.mark.g2
@pytest.mark.unit
def test_previous_key_too_short_rejected() -> None:
    """前一代密钥长度不足时启动失败."""

    future = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(TokenDigestValidationError, match="长度"):
        TokenDigestService(
            _ACCESS_KEY,
            _REFRESH_KEY,
            previous_access_key=b"short",
            rotation_expires_at=future,
        )


@pytest.mark.g2
@pytest.mark.unit
def test_previous_key_same_as_current_rejected() -> None:
    """前一代密钥不得与当前密钥相同."""

    future = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(TokenDigestValidationError, match="不得与当前密钥相同"):
        TokenDigestService(
            _ACCESS_KEY,
            _REFRESH_KEY,
            previous_access_key=_ACCESS_KEY,
            rotation_expires_at=future,
        )


@pytest.mark.g2
@pytest.mark.unit
def test_digest_always_uses_current_key() -> None:
    """digest 方法始终使用当前密钥，不受轮换影响 — SPEC 23.2.

    新 Token 始终用当前密钥计算摘要。
    """

    future = datetime.now(UTC) + timedelta(hours=1)
    service = TokenDigestService(
        _ACCESS_KEY,
        _REFRESH_KEY,
        previous_access_key=_PREV_ACCESS_KEY,
        previous_refresh_key=_PREV_REFRESH_KEY,
        rotation_expires_at=future,
    )

    # digest_access_token 的结果应与候选列表的第一个一致
    digest = service.digest_access_token("test")
    candidates = service.candidate_access_digests("test")
    assert digest == candidates[0]

    # 不应等于前一代密钥的摘要
    old_service = TokenDigestService(_PREV_ACCESS_KEY, _REFRESH_KEY)
    assert digest != old_service.digest_access_token("test")
