"""授权与安全测试（SPEC §13.3、§13.4、§23.5、VERIFY-074）。

验证授权架构的静态和动态安全属性，不依赖数据库或 Docker：

覆盖验收条件：
- 默认拒绝未认证访问（§23.5）
- 所有管理接口声明权限点（§23.5）
- 超级管理员检测基于角色标志而非魔法用户 ID（§13.4）
- 统一权限检查在请求入口执行（§13.3）
- 范围比较使用权限集运算（§13.3）

测试策略：
1. ``require_permission`` 依赖函数的动态行为测试（403/200/超级管理员绕过）
2. 未认证请求返回 401（默认拒绝）
3. 路由注册表的静态审查——每个非公共路由含权限检查依赖
4. 源代码静态扫描——无魔法用户 ID 比较
5. 超级管理员检测逻辑审查——基于 ``is_super_admin`` 角色标志
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.modules.rbac.application.port import AuthenticatedUser

pytestmark = [pytest.mark.g2, pytest.mark.security]


# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_authed_user(
    *,
    permissions: frozenset[str] = frozenset(),
    is_super_admin: bool = False,
) -> AuthenticatedUser:
    """构造测试用 AuthenticatedUser。"""
    return AuthenticatedUser(
        user_id=uuid4(),
        session_id=uuid4(),
        permissions=permissions,
        is_super_admin=is_super_admin,
        role_codes=frozenset(),
    )


def _collect_api_routes() -> list[APIRoute]:
    """从全部已启用模块的 Router 收集 APIRoute 对象。

    直接遍历模块定义中的 Router，而非 app.routes（后者在新版 FastAPI
    中使用延迟加载的 _IncludedRouter）。
    """
    from app.composition_root import get_enabled_modules

    routes: list[APIRoute] = []
    for module in get_enabled_modules():
        for router in module.routers:
            for route in router.routes:
                if isinstance(route, APIRoute):
                    routes.append(route)
    return routes


def _route_has_require_permission(route: APIRoute) -> bool:
    """检查路由是否声明了 require_permission 依赖。"""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False

    for dep in dependant.dependencies:
        name = getattr(getattr(dep, "call", None), "__name__", "")
        if name.startswith("require_permission_"):
            return True
    return False


def _route_has_get_current_user(route: APIRoute) -> bool:
    """检查路由是否使用了 get_current_user 依赖（自助端点）。"""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False

    for dep in dependant.dependencies:
        name = getattr(getattr(dep, "call", None), "__name__", "")
        if name == "get_current_user":
            return True
    return False


# 公共端点路径（不需要认证）——SPEC §23.5 显式声明
# G1 示例模块（/examples）为最小验证模块，不参与 RBAC（SPEC §30.2）
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/auth/login",
        "/auth/logout",
        "/auth/refresh",
        "/examples",
    }
)


def _is_public_route(path: str) -> bool:
    """判断路由是否为公共端点。"""
    return path in _PUBLIC_PATHS


# ===========================================================================
# require_permission 依赖函数行为测试（SPEC §13.3、§23.5）
# ===========================================================================


class TestRequirePermission:
    """require_permission 依赖函数测试（SPEC §13.3、§23.5）。"""

    async def test_user_with_permission_passes(self):
        """拥有所需权限的用户通过检查。"""
        from app.modules.rbac.dependencies import require_permission

        check = require_permission("system:role:read")
        user = _make_authed_user(permissions=frozenset({"system:role:read"}))
        result = await check(current_user=user)
        assert result.user_id == user.user_id

    async def test_user_without_permission_denied_403(self):
        """缺少所需权限的用户返回 403（SPEC §23.5）。"""
        from fastapi import HTTPException

        from app.modules.rbac.dependencies import require_permission

        check = require_permission("system:role:read")
        user = _make_authed_user(permissions=frozenset({"other:permission"}))
        with pytest.raises(HTTPException) as exc_info:
            await check(current_user=user)
        assert exc_info.value.status_code == 403

    async def test_super_admin_bypasses_permission_check(self):
        """超级管理员绕过权限检查（SPEC §13.4：集中式绕过）。"""
        from app.modules.rbac.dependencies import require_permission

        check = require_permission("system:role:create")
        super_user = _make_authed_user(
            permissions=frozenset(),  # 无任何权限点
            is_super_admin=True,
        )
        result = await check(current_user=super_user)
        assert result.is_super_admin is True

    async def test_or_semantics_any_matching_permission_passes(self):
        """require_permission 使用 OR 语义——任一匹配即通过。"""
        from app.modules.rbac.dependencies import require_permission

        check = require_permission("system:role:read", "system:role:create")
        user = _make_authed_user(permissions=frozenset({"system:role:create"}))
        result = await check(current_user=user)
        assert result.user_id == user.user_id

    async def test_empty_permissions_user_denied(self):
        """无任何权限的用户被拒绝（默认拒绝）。"""
        from fastapi import HTTPException

        from app.modules.rbac.dependencies import require_permission

        check = require_permission("system:user:read")
        user = _make_authed_user(permissions=frozenset())
        with pytest.raises(HTTPException) as exc_info:
            await check(current_user=user)
        assert exc_info.value.status_code == 403

    def test_require_permission_at_least_one_code(self):
        """require_permission 至少需要一个权限编码。"""
        from app.modules.rbac.dependencies import require_permission

        with pytest.raises(ValueError, match="至少需要一个权限编码"):
            require_permission()


# ===========================================================================
# 默认拒绝——未认证访问返回 401（SPEC §23.5）
# ===========================================================================


class TestDefaultDenyUnauthenticated:
    """默认拒绝未认证访问测试（SPEC §23.5）。"""

    def test_protected_endpoint_without_token_returns_401(self, app: FastAPI):
        """受保护端点在无 Token 时返回 401（SPEC §23.5）。"""
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            response = client.get("/api/v1/roles")
            assert response.status_code == 401

    def test_user_admin_endpoint_without_token_returns_401(self, app: FastAPI):
        """用户管理端点在无 Token 时返回 401。"""
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            response = client.get("/api/v1/users")
            assert response.status_code == 401

    def test_auth_session_endpoint_without_token_returns_401(self, app: FastAPI):
        """认证会话端点在无 Token 时返回 401。"""
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            response = client.get("/api/v1/auth/sessions")
            assert response.status_code == 401

    def test_invalid_bearer_token_returns_401(self, app: FastAPI):
        """无效 Bearer Token 返回 401。"""
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            response = client.get(
                "/api/v1/roles",
                headers={"Authorization": "Bearer invalid_token_here"},
            )
            assert response.status_code in (401, 503)


# ===========================================================================
# 路由权限声明静态审查（SPEC §23.5、VERIFY-074）
# ===========================================================================


class TestRoutePermissionDeclarations:
    """路由权限声明静态审查（SPEC §23.5、VERIFY-074）。

    通过直接遍历模块定义中的 Router 和 APIRoute 对象，
    检查每个非公共路由是否声明了权限检查依赖。
    """

    def test_all_non_public_routes_declare_permissions(self):
        """每个非公共路由至少声明一个权限点（SPEC §23.5、VERIFY-074）。

        公共端点（login、logout、refresh）不需要权限声明。
        所有其他端点必须通过 require_permission 或 get_current_user 声明认证。
        """
        routes = _collect_api_routes()
        assert len(routes) > 0, "未找到任何 API 路由——模块装配异常"

        unprotected: list[str] = []
        for route in routes:
            path = route.path
            if _is_public_route(path):
                continue

            if not _route_has_require_permission(route) and not _route_has_get_current_user(route):
                unprotected.append(f"{list(route.methods or set())} {path}")

        assert not unprotected, f"以下非公共路由缺少权限检查依赖: {unprotected}"

    def test_rbac_routes_all_protected(self):
        """RBAC 模块所有路由都声明了 require_permission。"""
        routes = _collect_api_routes()

        rbac_role_routes = [
            r for r in routes if r.path.startswith("/roles") and _route_has_require_permission(r)
        ]
        rbac_user_role_routes = [
            r
            for r in routes
            if "/roles" in r.path
            and r.path.startswith("/users")
            and _route_has_require_permission(r)
        ]

        total_protected = len(rbac_role_routes) + len(rbac_user_role_routes)
        # RBAC 模块有 12 个端点（9 个 roles + 3 个 user-role）
        assert total_protected == 12, (
            f"RBAC 路由权限声明不足，仅找到 {total_protected} 个受保护路由"
        )

    def test_user_admin_routes_all_protected(self):
        """用户模块管理端点都声明了 require_permission。"""
        routes = _collect_api_routes()

        user_admin_routes = [
            r
            for r in routes
            if r.path.startswith("/users") and "/roles" not in r.path and "/sessions" not in r.path
        ]
        assert len(user_admin_routes) > 0
        for route in user_admin_routes:
            assert _route_has_require_permission(route), f"用户管理路由缺少权限声明: {route.path}"

    def test_user_self_routes_use_get_current_user(self):
        """用户模块自助端点使用 get_current_user（已认证但不需特定权限）。"""
        routes = _collect_api_routes()

        self_routes = [r for r in routes if r.path.startswith("/me")]
        assert len(self_routes) >= 3
        for route in self_routes:
            assert _route_has_get_current_user(route), f"自助路由缺少认证依赖: {route.path}"

    def test_auth_session_routes_all_protected(self):
        """认证模块会话管理端点声明了 require_permission。"""
        routes = _collect_api_routes()

        session_routes = [r for r in routes if "/sessions" in r.path and r.path.startswith("/auth")]
        assert len(session_routes) >= 2
        for route in session_routes:
            assert _route_has_require_permission(route), f"会话管理路由缺少权限声明: {route.path}"


# ===========================================================================
# 超级管理员检测——无魔法用户 ID（SPEC §13.4、VERIFY-074）
# ===========================================================================


class TestSuperAdminDetection:
    """超级管理员检测方式审查（SPEC §13.4、VERIFY-074）。

    超级管理员必须通过角色标志 ``is_super_admin`` 检测，
    禁止使用魔法用户 ID 比较。
    """

    def test_super_admin_based_on_role_flag(self):
        """超级管理员检测基于 Role.is_super_admin 标志（SPEC §13.4）。"""
        from app.modules.rbac.domain.model import Role

        # 正常角色
        normal_role = Role.new(code="admin", name="管理员", current_time=datetime.now(UTC))
        assert normal_role.is_super_admin is False

        # 超级管理员角色
        super_role = Role.new(
            code="super_admin",
            name="超级管理员",
            is_super_admin=True,
            current_time=datetime.now(UTC),
        )
        assert super_role.is_super_admin is True

    def test_authenticated_user_has_super_admin_flag(self):
        """AuthenticatedUser 包含 is_super_admin 布尔标志。"""
        user = _make_authed_user(is_super_admin=True)
        assert user.is_super_admin is True

        user2 = _make_authed_user(is_super_admin=False)
        assert user2.is_super_admin is False

    def test_no_hardcoded_user_id_in_service(self):
        """RbacService 中无硬编码的用户 ID 比较（SPEC §13.4）。

        通过扫描服务源码确认超级管理员检测不使用魔法 UUID 比较。
        """
        import inspect

        from app.modules.rbac.application import service as svc_module

        source = inspect.getsource(svc_module)
        # 检查不存在 user_id == "固定 UUID" 或类似模式
        forbidden_patterns = [
            'user_id == "00000000',
            "user_id == uuid.UUID",
            'id == "00000000',
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, f"发现魔法用户 ID 比较: {pattern}"

    def test_super_admin_check_uses_role_flag(self):
        """_is_super_admin_in_uow 使用 role.is_super_admin 判断（SPEC §13.4）。"""
        import inspect

        from app.modules.rbac.application.service import RbacService

        source = inspect.getsource(RbacService._is_super_admin_in_uow)
        assert "is_super_admin" in source
        assert "role.is_super_admin" in source

    def test_require_permission_uses_is_super_admin_flag(self):
        """require_permission 通过 is_super_admin 标志绕过（SPEC §13.4）。"""
        import inspect

        from app.modules.rbac.dependencies import require_permission

        source = inspect.getsource(require_permission)
        assert "is_super_admin" in source


# ===========================================================================
# 范围比较使用权限集运算（SPEC §13.3、VERIFY-074）
# ===========================================================================


class TestScopeComparison:
    """管理范围比较逻辑审查（SPEC §13.3、VERIFY-074）。"""

    def test_permission_scope_uses_set_operations(self):
        """权限范围检查使用集合运算（SPEC §13.3）。"""
        import inspect

        from app.modules.rbac.application.service import RbacService

        source = inspect.getsource(RbacService._enforce_scope_for_permission_grant)
        # 验证使用集合差集运算
        assert "-" in source or "difference" in source or "issubset" in source, (
            "权限范围检查未使用集合运算"
        )

    def test_role_grant_uses_set_operations(self):
        """角色授予权限检查使用集合运算。"""
        import inspect

        from app.modules.rbac.application.service import RbacService

        source = inspect.getsource(RbacService._enforce_scope_for_role_grant)
        assert "-" in source or "issubset" in source, "角色授予权限检查未使用集合运算"

    def test_target_subset_uses_set_operations(self):
        """目标用户范围子集检查使用集合运算。"""
        import inspect

        from app.modules.rbac.application.service import RbacService

        source = inspect.getsource(RbacService._check_target_subset)
        assert "-" in source or "issubset" in source, "目标用户子集检查未使用集合运算"

    def test_actor_scope_is_permissions_union(self):
        """操作者范围查询返回权限点并集（frozenset[str]）。"""
        # get_for_user 返回 frozenset[str]（权限编码并集）
        import inspect

        from app.modules.rbac.application.port import RolePermissionRepository

        sig = inspect.signature(RolePermissionRepository.get_for_user)
        return_annotation = str(sig.return_annotation)
        assert "frozenset" in return_annotation


# ===========================================================================
# AuthenticatedUser 安全属性测试
# ===========================================================================


class TestAuthenticatedUserSecurity:
    """AuthenticatedUser 上下文安全测试。"""

    def test_authenticated_user_is_frozen(self):
        """AuthenticatedUser 不可变（防止运行时篡改权限集合）。"""
        from dataclasses import FrozenInstanceError

        user = _make_authed_user()
        with pytest.raises(FrozenInstanceError):
            user.is_super_admin = True  # type: ignore[misc]

    def test_permissions_are_frozenset(self):
        """permissions 字段为 frozenset（不可变集合）。"""
        user = _make_authed_user(permissions=frozenset({"a:b:c"}))
        assert isinstance(user.permissions, frozenset)

    def test_role_codes_are_frozenset(self):
        """role_codes 字段为 frozenset。"""
        user = _make_authed_user()
        assert isinstance(user.role_codes, frozenset)
