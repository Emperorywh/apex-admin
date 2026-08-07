"""Cookie 安全与 Origin 校验测试（SPEC §12.4、§28.3）。

验证 Refresh Token Cookie 的安全属性（``__Host-`` 前缀要求）和
Origin 校验 CSRF 防护机制。

覆盖验收条件：
- Cookie 属性：__Host-apex_refresh、Secure、HttpOnly、SameSite=Strict、Path=/、无 Domain
- 本地开发通过 localhost 使用 Secure Cookie；不提供关闭 Secure 的配置开关
- Refresh/Logout 校验 Origin 是否精确匹配部署配置白名单
- 跨站请求拒绝
- 业务接口不接受 Cookie 作为认证凭证（Bearer header）
"""

from __future__ import annotations

import pytest
from fastapi import Response

from app.config.settings import AppEnv, Settings
from app.modules.auth.middleware.origin import validate_origin
from app.modules.auth.routes import (
    REFRESH_TOKEN_COOKIE_NAME,
    _delete_refresh_token_cookie,
    _set_refresh_token_cookie,
)

pytestmark = [pytest.mark.g2, pytest.mark.security]

# 测试用有效密钥（与 conftest 一致）
_VALID_ACCESS_KEY = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
_VALID_REFRESH_KEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
_VALID_ENCRYPTION_KEY = "f0e1d2c3b4a5968778695a4b3c2d1e0ff0e1d2c3b4a5968778695a4b3c2d1e0f"


def _make_test_settings(allowed_origins: list[str] | None = None) -> Settings:
    """构造测试用 Settings。"""
    return Settings(
        _env_file=None,
        app_env=AppEnv.TESTING,
        database_url="postgresql+psycopg://apex:secret@localhost:5432/apex_admin_test",
        access_token_hmac_key=_VALID_ACCESS_KEY,
        refresh_token_hmac_key=_VALID_REFRESH_KEY,
        config_encryption_key=_VALID_ENCRYPTION_KEY,
        file_storage_root="/tmp/apex-test-files",
        allowed_origins=allowed_origins or ["http://localhost:3000"],
    )


# ===========================================================================
# Cookie 属性测试（SPEC §12.4：__Host- 前缀要求）
# ===========================================================================


class TestRefreshTokenCookieAttributes:
    """Refresh Token Cookie 安全属性测试（SPEC §12.4）。"""

    def test_cookie_name_is_host_prefixed(self) -> None:
        """Cookie 名称为 ``__Host-apex_refresh``（SPEC §12.4）。"""
        assert REFRESH_TOKEN_COOKIE_NAME == "__Host-apex_refresh"

    def test_set_cookie_has_secure_attribute(self) -> None:
        """Cookie 设置 ``Secure`` 属性（SPEC §12.4）。"""
        response = Response()
        _set_refresh_token_cookie(response, "test-token")

        cookies = response.headers.getlist("set-cookie")
        assert any("Secure" in c for c in cookies)

    def test_set_cookie_has_httponly_attribute(self) -> None:
        """Cookie 设置 ``HttpOnly`` 属性（SPEC §12.4）。"""
        response = Response()
        _set_refresh_token_cookie(response, "test-token")

        cookies = response.headers.getlist("set-cookie")
        assert any("HttpOnly" in c for c in cookies)

    def test_set_cookie_has_samesite_strict(self) -> None:
        """Cookie 设置 ``SameSite=Strict``（SPEC §12.4）。"""
        response = Response()
        _set_refresh_token_cookie(response, "test-token")

        cookies = response.headers.getlist("set-cookie")
        assert any("samesite=strict" in c.lower() for c in cookies)

    def test_set_cookie_has_path_root(self) -> None:
        """Cookie 设置 ``Path=/``（SPEC §12.4）。"""
        response = Response()
        _set_refresh_token_cookie(response, "test-token")

        cookies = response.headers.getlist("set-cookie")
        assert any("Path=/" in c for c in cookies)

    def test_set_cookie_has_no_domain(self) -> None:
        """Cookie 不设置 ``Domain``（SPEC §12.4）。"""
        response = Response()
        _set_refresh_token_cookie(response, "test-token")

        cookies = response.headers.getlist("set-cookie")
        for cookie in cookies:
            assert "domain=" not in cookie.lower()

    def test_set_cookie_has_all_required_attributes(self) -> None:
        """Cookie 同时具备全部必需属性（SPEC §12.4：__Host- 前缀要求）。"""
        response = Response()
        _set_refresh_token_cookie(response, "test-token")

        cookies = response.headers.getlist("set-cookie")
        assert len(cookies) == 1
        cookie_str = cookies[0]
        assert "__Host-apex_refresh" in cookie_str
        assert "Secure" in cookie_str
        assert "HttpOnly" in cookie_str
        assert "samesite=strict" in cookie_str.lower()
        assert "Path=/" in cookie_str
        assert "domain=" not in cookie_str.lower()

    def test_delete_cookie_uses_same_attributes(self) -> None:
        """删除 Cookie 使用与设置时相同的属性（SPEC §12.4）。"""
        response = Response()
        _delete_refresh_token_cookie(response)

        cookies = response.headers.getlist("set-cookie")
        assert len(cookies) == 1
        cookie_str = cookies[0]
        assert "__Host-apex_refresh" in cookie_str
        assert "Secure" in cookie_str
        assert "HttpOnly" in cookie_str
        assert "samesite=strict" in cookie_str.lower()
        assert "Path=/" in cookie_str
        assert "domain=" not in cookie_str.lower()


# ===========================================================================
# Secure 配置不可关闭测试（SPEC §12.4）
# ===========================================================================


class TestSecureCookieConfig:
    """Secure Cookie 不可关闭配置测试（SPEC §12.4）。

    SPEC §12.4 明确规定：不提供关闭 ``Secure`` 的配置开关。
    """

    def test_settings_has_no_secure_cookie_field(self) -> None:
        """Settings 不包含 ``secure_cookie`` 或类似配置字段（SPEC §12.4）。"""
        field_names = set(Settings.model_fields.keys())
        forbidden_names = {
            "secure_cookie",
            "cookie_secure",
            "disable_secure_cookie",
            "allow_insecure_cookie",
        }
        intersection = field_names & forbidden_names
        assert intersection == set(), f"存在禁止的 Cookie 安全开关: {intersection}"


# ===========================================================================
# Origin 校验测试（SPEC §12.4：CSRF 防护）
# ===========================================================================


class TestOriginValidation:
    """Origin 校验测试（SPEC §12.4）。

    Refresh、Logout 等读取 Cookie 的状态变更接口必须校验 Origin
    是否精确匹配部署配置白名单。
    """

    def test_valid_origin_passes(self) -> None:
        """白名单中的 Origin 通过校验。"""
        from starlette.requests import Request as StarletteRequest

        settings = _make_test_settings(["http://localhost:3000"])

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"origin", b"http://localhost:3000")],
        }
        request = StarletteRequest(scope)
        # 不抛出异常即表示通过
        validate_origin(request, settings)

    def test_invalid_origin_rejected(self) -> None:
        """不在白名单的 Origin 被拒绝。"""
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest

        settings = _make_test_settings(["http://localhost:3000"])
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"origin", b"https://evil.com")],
        }
        request = StarletteRequest(scope)

        with pytest.raises(HTTPException) as exc_info:
            validate_origin(request, settings)
        assert exc_info.value.status_code == 403

    def test_missing_origin_rejected(self) -> None:
        """缺失 Origin 头的请求被拒绝。"""
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest

        settings = _make_test_settings(["http://localhost:3000"])
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [],
        }
        request = StarletteRequest(scope)

        with pytest.raises(HTTPException) as exc_info:
            validate_origin(request, settings)
        assert exc_info.value.status_code == 403

    def test_cross_site_origin_rejected(self) -> None:
        """跨站 Origin 被拒绝（SPEC §12.4：跨站部署须 ADR）。"""
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest

        settings = _make_test_settings(["https://admin.example.com"])
        # 攻击者从不同域名发起请求
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"origin", b"https://attacker.evil.com")],
        }
        request = StarletteRequest(scope)

        with pytest.raises(HTTPException) as exc_info:
            validate_origin(request, settings)
        assert exc_info.value.status_code == 403

    def test_exact_match_required_no_subdomain(self) -> None:
        """Origin 精确匹配——子域名不算匹配（SPEC §12.4：精确匹配）。"""
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest

        settings = _make_test_settings(["https://admin.example.com"])
        # 子域名不应匹配
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"origin", b"https://sub.admin.example.com")],
        }
        request = StarletteRequest(scope)

        with pytest.raises(HTTPException) as exc_info:
            validate_origin(request, settings)
        assert exc_info.value.status_code == 403

    def test_multiple_allowed_origins(self) -> None:
        """多白名单 Origin 都能通过。"""
        from starlette.requests import Request as StarletteRequest

        settings = _make_test_settings(
            ["https://admin.example.com", "http://localhost:3000"],
        )
        for origin in [b"https://admin.example.com", b"http://localhost:3000"]:
            scope = {
                "type": "http",
                "method": "POST",
                "headers": [(b"origin", origin)],
            }
            request = StarletteRequest(scope)
            validate_origin(request, settings)  # 不抛出异常即通过


# ===========================================================================
# 业务接口认证方式测试（SPEC §12.4：业务接口不接受 Cookie 认证）
# ===========================================================================


class TestBusinessRoutesUseBearerAuth:
    """业务接口认证方式测试（SPEC §12.4）。

    除 Refresh Token Cookie 外，业务接口不得接受 Cookie 作为认证凭证。
    业务路由认证依赖使用 Bearer header（Authorization: Bearer <token>）。
    """

    def test_auth_context_uses_bearer_header(self) -> None:
        """``get_auth_context`` 从 ``Authorization: Bearer`` 头读取 Token（SPEC §12.4）。

        静态验证：认证依赖不使用 Cookie 参数获取 Access Token。
        """
        import inspect

        from app.modules.auth.routes import get_auth_context

        source = inspect.getsource(get_auth_context)
        # 确认使用 Authorization 头
        assert "authorization" in source.lower()
        assert "Bearer" in source
        # 确认不使用 Cookie 参数获取 Access Token
        assert "Cookie" not in source or "cookie" not in source.lower()

    def test_session_endpoints_require_bearer_auth(self) -> None:
        """会话管理端点使用 Bearer 认证依赖而非 Cookie（SPEC §12.4）。"""
        import inspect

        from app.modules.auth.routes import list_sessions

        source = inspect.getsource(list_sessions)
        # 确认使用 get_auth_context 依赖
        assert "get_auth_context" in source
