"""认证模块常量 — SPEC 12.1 / 12.2 / 12.3 / 12.4.

所有时间与限制常量集中管理，便于测试引用和未来 ADR 变更审查。
SPEC 12.1: Access Token 默认 15 分钟。
SPEC 12.2: Refresh Token 轮换与重放检测。
SPEC 12.3: 空闲 30 分钟、绝对 12 小时、活动时间 5 分钟条件更新。
SPEC 12.4: 账号失败 5 次限制 15 分钟、IP 失败 20 次限制 15 分钟。
"""

from __future__ import annotations

from datetime import timedelta

# ── Token 与会话过期规则（SPEC 12.1 / 12.3）─────────────────────────────────

#: Access Token 默认有效期 — 15 分钟（SPEC 12.1）。
ACCESS_TOKEN_TTL: timedelta = timedelta(minutes=15)

#: 会话空闲过期时间 — 30 分钟无活动（SPEC 12.3）。
SESSION_IDLE_TIMEOUT: timedelta = timedelta(minutes=30)

#: 会话绝对过期时间 — 12 小时（SPEC 12.3）。
SESSION_ABSOLUTE_TIMEOUT: timedelta = timedelta(hours=12)

#: 最近活动时间条件更新间隔 — 5 分钟内不重复写库（SPEC 12.3）。
ACTIVITY_UPDATE_INTERVAL: timedelta = timedelta(minutes=5)

# ── 登录失败限制（SPEC 12.4）──────────────────────────────────────────────

#: 账号维度连续失败上限 — 5 次后限制 15 分钟（SPEC 12.4）。
ACCOUNT_FAILURE_LIMIT: int = 5

#: 可信客户端 IP 维度连续失败上限 — 20 次后限制 15 分钟（SPEC 12.4）。
IP_FAILURE_LIMIT: int = 20

#: 失败锁定持续时间 — 15 分钟（SPEC 12.4）。
FAILURE_LOCK_DURATION: timedelta = timedelta(minutes=15)

# ── 失败维度编码 — SPEC 12.4 ────────────────────────────────────────────────

#: 账号维度标识。
DIMENSION_ACCOUNT: str = "account"

#: 可信客户端 IP 维度标识。
DIMENSION_IP: str = "ip"

# ── Refresh Token Cookie 与轮换（SPEC 12.2 / 12.4）─────────────────────────

#: Refresh Token Cookie 固定名称 — ``__Host-`` 前缀要求 Secure、Path=/、无 Domain
#: （SPEC 12.4）。
REFRESH_COOKIE_NAME: str = "__Host-apex_refresh"

#: Refresh Token Cookie 属性 — 固定值（SPEC 12.4）。
REFRESH_COOKIE_SAMESITE: str = "strict"
REFRESH_COOKIE_PATH: str = "/"

#: Refresh Token 吊销原因编码。
REFRESH_REVOKE_ROTATED: str = "rotated"
REFRESH_REVOKE_REPLAY: str = "replay_detected"
REFRESH_REVOKE_LOGOUT: str = "user_logout"
REFRESH_REVOKE_SESSION_REVOKED: str = "session_revoked"
