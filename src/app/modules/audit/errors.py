"""审计查询模块错误码与异常 — SPEC 10.1 / 10.2 / 18.3.

SPEC 10.2:
  - 错误码全局唯一且稳定。
  - 错误码按 ``<MODULE>.<REASON>`` 格式划分命名空间。
  - 错误码与展示文案分离。

模块错误码在导入时注册到框架默认注册表 ``default_registry``。
"""

from __future__ import annotations

from app.core.errors.codes import default_registry
from app.core.errors.exceptions import NotFoundError

# ── 错误码常量 ──────────────────────────────────────────────────────────────

#: 审计日志不存在 — 按 ID 查询审计日志但未找到。
AUDIT_LOG_NOT_FOUND = "AUDIT.LOG_NOT_FOUND"

#: 登录日志不存在 — 按 ID 查询登录日志但未找到。
AUDIT_LOGIN_LOG_NOT_FOUND = "AUDIT.LOGIN_LOG_NOT_FOUND"


# ── 模块异常类 ──────────────────────────────────────────────────────────────


class AuditLogNotFoundError(NotFoundError):
    """审计日志不存在 — HTTP 404."""

    code = AUDIT_LOG_NOT_FOUND


class LoginLogNotFoundError(NotFoundError):
    """登录日志不存在 — HTTP 404."""

    code = AUDIT_LOGIN_LOG_NOT_FOUND


# ── 注册到框架默认注册表 ──────────────────────────────────────────────────

default_registry.register(
    AUDIT_LOG_NOT_FOUND,
    404,
    meaning="审计日志不存在",
    scenario="按 ID 查询审计日志详情但未找到",
)
default_registry.register(
    AUDIT_LOGIN_LOG_NOT_FOUND,
    404,
    meaning="登录日志不存在",
    scenario="按 ID 查询登录日志详情但未找到",
)
