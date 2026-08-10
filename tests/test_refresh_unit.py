"""Refresh Token Cookie 属性、Origin 校验与常量单位测试 — SPEC 12.2 / 12.4.

不依赖数据库连接的纯逻辑验证。
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.modules.auth.constants import (
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    REFRESH_COOKIE_SAMESITE,
)

# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: Cookie 常量属性验证 — SPEC 12.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestRefreshCookieConstants:
    """Refresh Token Cookie 固定属性 — SPEC 12.4."""

    def test_cookie_name_is_host_prefixed(self) -> None:
        """Cookie 名称为 ``__Host-apex_refresh`` — SPEC 12.4."""

        assert REFRESH_COOKIE_NAME == "__Host-apex_refresh"

    def test_cookie_samesite_is_strict(self) -> None:
        """SameSite=Strict — SPEC 12.4."""

        assert REFRESH_COOKIE_SAMESITE == "strict"

    def test_cookie_path_is_root(self) -> None:
        """Path=/ — SPEC 12.4."""

        assert REFRESH_COOKIE_PATH == "/"


@pytest.mark.g2
@pytest.mark.unit
class TestRefreshCookieAttributes:
    """``set_cookie`` / ``delete_cookie`` 设置的属性 — SPEC 12.4."""

    def test_set_cookie_has_all_required_attributes(self) -> None:
        """``__Host-`` 前缀要求 Secure、HttpOnly、Path=/、无 Domain — SPEC 12.4."""

        from starlette.responses import Response

        from app.modules.auth.constants import SESSION_ABSOLUTE_TIMEOUT
        from app.modules.auth.router import _set_refresh_cookie

        response = Response()
        _set_refresh_cookie(response, "test-refresh-token-value")

        cookie_header = response.headers.get("set-cookie", "")
        assert "__Host-apex_refresh=test-refresh-token-value" in cookie_header
        assert "Secure" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "SameSite=strict" in cookie_header
        assert "Path=/" in cookie_header
        # __Host- 前缀不得设置 Domain
        assert "Domain=" not in cookie_header
        # max_age 与会话绝对过期一致
        expected_max_age = int(SESSION_ABSOLUTE_TIMEOUT.total_seconds())
        assert f"Max-Age={expected_max_age}" in cookie_header

    def test_delete_cookie_has_same_attributes(self) -> None:
        """删除 Cookie 使用相同属性 — SPEC 12.4."""

        from starlette.responses import Response

        from app.modules.auth.router import _delete_refresh_cookie

        response = Response()
        _delete_refresh_cookie(response)

        cookie_header = response.headers.get("set-cookie", "")
        assert "__Host-apex_refresh=" in cookie_header
        # 删除 Cookie 设置为空值或过期
        assert "Max-Age=0" in cookie_header or "expires=" in cookie_header.lower()
        assert "Secure" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "SameSite=strict" in cookie_header
        assert "Path=/" in cookie_header
        # __Host- 前缀不得设置 Domain
        assert "Domain=" not in cookie_header


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: Origin 白名单解析 — SPEC 12.4
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestAllowedOriginsParsing:
    """Origin 白名单解析 — SPEC 12.4."""

    def test_default_localhost_in_whitelist(self) -> None:
        """默认 Origin 白名单包含 localhost。"""

        settings = Settings()
        assert "http://localhost" in settings.allowed_origin_set

    def test_comma_separated_origins(self) -> None:
        """逗号分隔的 Origin 列表正确解析。"""

        settings = Settings(
            ALLOWED_ORIGINS="http://localhost:3000,https://admin.example.com",
        )
        assert "http://localhost:3000" in settings.allowed_origin_set
        assert "https://admin.example.com" in settings.allowed_origin_set
        assert len(settings.allowed_origin_set) == 2

    def test_whitespace_stripped(self) -> None:
        """Origin 列表中的空白被去除。"""

        settings = Settings(
            ALLOWED_ORIGINS=" http://a.com , http://b.com ",
        )
        assert "http://a.com" in settings.allowed_origin_set
        assert "http://b.com" in settings.allowed_origin_set

    def test_empty_origins_ignored(self) -> None:
        """空 Origin 条目被忽略。"""

        settings = Settings(
            ALLOWED_ORIGINS="http://a.com,,http://b.com,",
        )
        assert len(settings.allowed_origin_set) == 2

    def test_origin_not_in_whitelist(self) -> None:
        """不在白名单的 Origin 不被接受。"""

        settings = Settings(ALLOWED_ORIGINS="http://localhost")
        assert "https://evil.com" not in settings.allowed_origin_set


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: Refresh Token 摘要不进入日志/响应
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestRefreshTokenNotInJsonResponse:
    """Refresh Token 不进入 JSON 响应 — SPEC 12.2 / 12.4."""

    def test_refresh_response_schema_no_refresh_field(self) -> None:
        """``RefreshResponse`` 不包含 refresh_token 字段 — SPEC 12.2."""

        from app.modules.auth.schemas import RefreshResponse

        fields = set(RefreshResponse.model_fields.keys())
        assert "refresh_token" not in fields
        assert "access_token" in fields
        assert "expires_in" in fields

    def test_login_response_schema_no_refresh_field(self) -> None:
        """``LoginResponse`` 不包含 refresh_token 字段 — SPEC 12.2."""

        from app.modules.auth.schemas import LoginResponse

        fields = set(LoginResponse.model_fields.keys())
        assert "refresh_token" not in fields


# ═══════════════════════════════════════════════════════════════════════════════
# 错误码验证 — SPEC 10.2
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.unit
class TestRefreshErrorCodes:
    """刷新相关错误码 — SPEC 10.2."""

    def test_refresh_failed_code_registered(self) -> None:
        """AUTH.REFRESH_FAILED 已注册到注册表。"""

        from app.core.errors.codes import default_registry
        from app.modules.auth.errors import AUTH_REFRESH_FAILED

        metadata = default_registry.get(AUTH_REFRESH_FAILED)
        assert metadata is not None
        assert metadata.http_status == 401

    def test_refresh_failed_error_is_authentication(self) -> None:
        """RefreshFailedError 继承 AuthenticationError。"""

        from app.core.errors.exceptions import AuthenticationError
        from app.modules.auth.errors import RefreshFailedError

        assert issubclass(RefreshFailedError, AuthenticationError)
