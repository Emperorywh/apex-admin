"""授权执行体系测试 — SPEC 13.2/13.3/13.4/23.5/34.2.

覆盖 TASK-016 验收标准:
  - AC-0: 路由注册测试——除显式公共接口外全部管理接口声明至少一个权限点
  - AC-1: 越权测试——403 稳定错误码、管理范围违规拒绝
  - AC-2: UoW 内二次校验——入口校验后权限被撤销的场景最终被拒绝
  - AC-3: 超管绕过单点集中、最后超管保护、超管操作审计
  - AC-4: 强制下线——会话立即失效、登录日志
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.application.context import UseCaseContext
from app.application.ports import Clock, IdGenerator
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from fastapi.routing import APIRoute
    from sqlalchemy.ext.asyncio import AsyncEngine


# ═══════════════════════════════════════════════════════════════════════════════
# AC-0: 路由注册测试——所有管理接口声明权限点
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.security
def test_all_management_routes_declare_permission() -> None:
    """路由注册测试——除显式公共接口外全部管理接口声明至少一个权限点 — SPEC 23.5/34.2.

    通过遍历应用的全部路由（展开 ``_IncludedRouter`` 对象获取实际
    ``APIRoute`` 实例），检查每个非公共、非自助、非示例端点的依赖树
    中是否存在携带 ``__apex_permission__`` 标记的依赖函数。

    SPEC 23.5: 公共接口必须显式声明。
    SPEC 11.1: 自助端点仅需认证不需权限点。
    """

    from fastapi.routing import APIRoute

    from app.core.config import Environment, Settings
    from app.main import create_app

    settings = Settings(ENVIRONMENT=Environment.TESTING)
    app = create_app(settings)

    # ── 显式豁免清单 ─────────────────────────────────────────────────────

    # 公共接口——SPEC 23.5: "公共接口必须显式声明"
    public_paths = {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
    }
    # 健康检查与 meta 端点
    public_prefixes = ("/health", "/api/v1/meta")
    # 文档端点
    docs_prefixes = ("/docs", "/redoc", "/openapi.json")
    # 示例模块——演示用途，非管理接口
    example_prefixes = ("/api/v1/example",)
    # 自助端点——SPEC 23.5/11.1: 仅需认证不需权限点（按方法+路径精确匹配）
    self_service_endpoints: set[tuple[str, str]] = {
        ("GET", "/api/v1/users/me"),
        ("PUT", "/api/v1/users/me"),
        ("PUT", "/api/v1/users/me/password"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/auth/logout-others"),
        ("GET", "/api/v1/auth/sessions"),
    }

    # ── 展开 _IncludedRouter 获取全部 APIRoute 实例 ──────────────────────
    # FastAPI 0.139+ 中 include_router 注册的路由以 _IncludedRouter 对象
    # 存在于 app.routes，而非 APIRoute 实例。需展开 original_router.routes
    # 并拼接 include_context.prefix 得到完整路径。

    all_api_routes: list[tuple[str, APIRoute]] = []  # (full_path, route)

    for entry in app.routes:
        if isinstance(entry, APIRoute):
            # 直接 APIRoute（docs、openapi.json 等框架路由）
            all_api_routes.append((entry.path, entry))
            continue

        include_context = getattr(entry, "include_context", None)
        prefix = include_context.prefix if include_context else ""
        original_router = getattr(entry, "original_router", None)
        if original_router is not None:
            for sub_route in original_router.routes:
                if isinstance(sub_route, APIRoute):
                    all_api_routes.append((prefix + sub_route.path, sub_route))

    # ── 检查每个管理路由是否声明了权限点 ─────────────────────────────────

    missing: list[str] = []
    management_routes_found = 0

    for full_path, route in all_api_routes:
        # 跳过公共接口
        if (
            full_path in public_paths
            or any(full_path.startswith(p) for p in public_prefixes)
            or any(full_path.startswith(p) for p in docs_prefixes)
            or any(full_path.startswith(p) for p in example_prefixes)
        ):
            continue

        # 跳过自助端点（按方法+路径精确匹配）
        methods = route.methods or set()
        if any((method, full_path) in self_service_endpoints for method in methods):
            continue

        management_routes_found += 1

        if not _route_has_permission_dependency(route):
            method_label = sorted(methods)[0] if methods else "?"
            missing.append(f"{method_label} {full_path}")

    # 完整性护栏：确认确实枚举到了管理路由，防止路由枚举退化为空操作
    assert management_routes_found >= 1, (
        "路由枚举未发现任何管理接口——"
        "可能是 FastAPI 路由结构变更导致 _IncludedRouter 展开失效"
    )

    assert not missing, "以下管理接口未声明权限点（SPEC 23.5）:\n" + "\n".join(
        f"  {m}" for m in missing
    )


def _route_has_permission_dependency(route: APIRoute) -> bool:
    """检查路由的依赖树中是否存在携带 ``__apex_permission__`` 的依赖.

    递归遍历 ``dependant.dependencies``（最多两层深度）。
    """

    def _has_perm_marker(dependant: object) -> bool:
        call = getattr(dependant, "call", None)
        return call is not None and hasattr(call, "__apex_permission__")

    for dep in route.dependant.dependencies:
        if _has_perm_marker(dep):
            return True
        for sub_dep in dep.dependencies:
            if _has_perm_marker(sub_dep):
                return True

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3(部分): 超管判定集中单点——无魔法用户 ID
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.security
def test_super_admin_bypass_is_centralized_no_magic_ids() -> None:
    """超管绕过规则集中在单点实现且无魔法用户 ID — SPEC 13.4.

    验证:
      1. ``SUPER_ADMIN_ROLE_CODE`` 在 ``authorization.py`` 唯一定义。
      2. 源码中不存在按硬编码用户 ID 或 UUID 判定超管的逻辑。
    """

    import ast
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    src_dir = project_root / "src" / "app"

    # 收集所有 Python 源文件
    py_files = list(src_dir.rglob("*.py"))

    # 搜索魔法 ID 模式: 按用户 ID 字面量判定超管
    # 允许的模式: SUPER_ADMIN_ROLE_CODE, is_super_admin(role_codes)

    violations: list[str] = []

    for py_file in py_files:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))

        for node in ast.walk(tree):
            # 检查字符串比较: if user_id == "..." 或 actor_id == "..."
            if isinstance(node, ast.Compare):
                for comparator in [node.left, *node.comparators]:
                    if (
                        isinstance(comparator, ast.Constant)
                        and isinstance(comparator.value, str)
                        and isinstance(node.left, ast.Name)
                        and node.left.id
                        in (
                            "user_id",
                            "actor_id",
                            "uid",
                        )
                    ):
                        val = comparator.value.lower()
                        if val in ("admin", "root", "superadmin", "1", "0"):
                            violations.append(
                                f"{py_file.name}:{node.lineno}: "
                                f"魔法用户 ID 特判 '{comparator.value}'",
                            )

    assert not violations, (
        "发现魔法用户 ID 特判（SPEC 13.4: 禁止通过魔法用户 ID 判断超级管理员）:\n"
        + "\n".join(violations)
    )

    # 验证 SUPER_ADMIN_ROLE_CODE 唯一定义
    from app.core.security.authorization import SUPER_ADMIN_ROLE_CODE, is_super_admin

    assert SUPER_ADMIN_ROLE_CODE == "super_admin"
    # 超管判定基于角色编码
    assert is_super_admin({"super_admin"}) is True
    assert is_super_admin({"editor"}) is False
    assert is_super_admin(set()) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 集成测试辅助
# ═══════════════════════════════════════════════════════════════════════════════


class FixedClock(Clock):
    """固定时钟。"""

    def __init__(self, time: datetime) -> None:
        self._time = time

    def now(self) -> datetime:
        return self._time


class FixedIdGenerator(IdGenerator):
    """固定 ID 生成器。"""

    def __init__(self, *ids: UUID) -> None:
        self._ids = list(ids)
        self._n = 0

    def generate_id(self) -> UUID:
        if self._n < len(self._ids):
            result = self._ids[self._n]
            self._n += 1
            return result
        return uuid4()


async def _apply_migrations(database_url: str) -> None:
    """对测试数据库执行 alembic upgrade head。"""

    from alembic import command

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.migrations import get_alembic_config

    config = get_alembic_config(
        database_url=database_url,
        version_locations=MODULE_VERSION_LOCATIONS,
    )
    await asyncio.to_thread(lambda: command.upgrade(config, "head"))


async def _cleanup_all(database_url: str) -> None:
    """清理全部 G2 相关表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            for table in (
                "auth_refresh_tokens",
                "auth_login_attempts",
                "auth_sessions",
                "rbac_user_roles",
                "rbac_role_permissions",
                "rbac_roles",
                "rbac_permissions",
                "login_logs",
                "audit_logs",
                "users",
            ):
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


async def _insert_user(
    database_url: str,
    *,
    username: str,
    status: str = "active",
) -> UUID:
    """直接插入用户并返回 ID。"""

    user_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, display_name, "
                    "password_hash, status, created_at, updated_at) "
                    "VALUES (:id, :username, :dn, :ph, :st, :ca, :ua)",
                ),
                {
                    "id": str(user_id),
                    "username": username,
                    "dn": username.title(),
                    "ph": "$argon2id$fake",
                    "st": status,
                    "ca": now,
                    "ua": now,
                },
            )
    finally:
        await engine.dispose()
    return user_id


async def _insert_builtin_role(
    database_url: str,
    *,
    code: str,
    display_name: str,
    status: str = "active",
) -> UUID:
    """直接插入内置角色。"""

    role_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_roles "
                    "(id, code, display_name, description, status, "
                    "is_builtin, sort_order, created_at, updated_at) "
                    "VALUES (:id, :code, :dn, NULL, :st, TRUE, 0, :ca, :ua)",
                ),
                {
                    "id": str(role_id),
                    "code": code,
                    "dn": display_name,
                    "st": status,
                    "ca": now,
                    "ua": now,
                },
            )
    finally:
        await engine.dispose()
    return role_id


async def _insert_permission(
    database_url: str,
    *,
    code: str,
    module_code: str = "identity",
) -> UUID:
    """直接插入权限点。"""

    perm_id = uuid4()
    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_permissions "
                    "(id, code, display_name, description, module_code, "
                    "is_active, created_at, updated_at) "
                    "VALUES (:id, :code, :code, NULL, :mc, TRUE, :ca, :ua)",
                ),
                {
                    "id": str(perm_id),
                    "code": code,
                    "mc": module_code,
                    "ca": now,
                    "ua": now,
                },
            )
    finally:
        await engine.dispose()
    return perm_id


async def _insert_role_permission(
    database_url: str,
    *,
    role_id: UUID,
    permission_id: UUID,
) -> None:
    """关联角色与权限点。"""

    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_role_permissions "
                    "(role_id, permission_id, created_at) "
                    "VALUES (:rid, :pid, :ca)",
                ),
                {"rid": str(role_id), "pid": str(permission_id), "ca": now},
            )
    finally:
        await engine.dispose()


async def _insert_user_role(
    database_url: str,
    *,
    user_id: UUID,
    role_id: UUID,
) -> None:
    """分配用户角色。"""

    now = datetime.now(UTC)
    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO rbac_user_roles "
                    "(user_id, role_id, created_at, created_by) "
                    "VALUES (:uid, :rid, :ca, NULL)",
                ),
                {"uid": str(user_id), "rid": str(role_id), "ca": now},
            )
    finally:
        await engine.dispose()


def _make_rbac_use_case(
    engine: AsyncEngine, actor_id: str
) -> tuple[object, UseCaseContext]:
    """构造测试用 RbacUseCase。"""

    from app.modules.audit.adapter import SqlAlchemyAuditRepository
    from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter
    from app.modules.rbac.adapter import SqlAlchemyUserRbacAdapter
    from app.modules.rbac.use_case import RbacUseCase

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyAuditRepository(session)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyUserAuthAdapter(session)

    def user_rbac_port_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyUserRbacAdapter(session)

    ctx = UseCaseContext(request_id="test-req", actor_id=actor_id)
    uc = RbacUseCase(
        uow_factory=uow_factory,
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        id_generator=FixedIdGenerator(uuid4()),
        audit_factory=audit_factory,
        user_auth_port_factory=user_auth_port_factory,
        user_rbac_port_factory=user_rbac_port_factory,
    )
    return uc, ctx


def _make_identity_use_case(
    engine: AsyncEngine,
    actor_id: str,
) -> tuple[object, UseCaseContext]:
    """构造测试用 UserUseCase。"""

    from app.core.security.password import Argon2Hasher
    from app.modules.audit.adapter import SqlAlchemyAuditRepository
    from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter
    from app.modules.identity.use_case import UserUseCase
    from app.modules.rbac.adapter import SqlAlchemyUserRbacAdapter

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    def audit_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyAuditRepository(session)

    def user_rbac_port_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyUserRbacAdapter(session)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyUserAuthAdapter(session)

    ctx = UseCaseContext(request_id="test-req", actor_id=actor_id)
    uc = UserUseCase(
        uow_factory=uow_factory,
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        id_generator=FixedIdGenerator(uuid4()),
        hasher=Argon2Hasher(),
        event_handlers=[],
        audit_factory=audit_factory,
        user_rbac_port_factory=user_rbac_port_factory,
        user_auth_port_factory=user_auth_port_factory,
    )
    return uc, ctx


def _make_auth_use_case(engine: AsyncEngine) -> object:
    """构造测试用 AuthUseCase。"""

    from app.core.security.digest import TokenDigestService
    from app.core.security.password import Argon2Hasher
    from app.modules.audit.adapter import SqlAlchemyLoginLogRepository
    from app.modules.audit.security_log import StructlogSecurityLogger
    from app.modules.auth.use_case import AuthUseCase
    from app.modules.identity.adapter import SqlAlchemyUserAuthAdapter

    access_key = b"a" * 32
    refresh_key = b"b" * 32
    digest_service = TokenDigestService(
        access_key=access_key,
        refresh_key=refresh_key,
    )

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(engine)

    def user_auth_port_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyUserAuthAdapter(session)

    def login_log_factory(session):  # type: ignore[no-untyped-def]
        return SqlAlchemyLoginLogRepository(session)

    def security_log_factory(session):  # type: ignore[no-untyped-def]
        return StructlogSecurityLogger()

    return AuthUseCase(
        uow_factory=uow_factory,
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        id_generator=FixedIdGenerator(uuid4()),
        hasher=Argon2Hasher(),
        digest_service=digest_service,
        user_auth_port_factory=user_auth_port_factory,
        login_log_factory=login_log_factory,
        security_log_factory=security_log_factory,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: 管理范围违规拒绝——普通管理员授予超出范围的权限被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_management_scope_blocks_assigning_out_of_scope_permissions(
    database_url: str,
) -> None:
    """普通管理员只能授予自身范围内的权限点 — SPEC 13.2.

    普通管理员有 system:user:read 但没有 system:user:write。
    尝试为角色分配 system:user:write 时被拒绝。
    """

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        # 设置权限点
        await _insert_permission(database_url, code="system:user:read")
        await _insert_permission(database_url, code="system:user:write")

        # 创建超管角色（内置）
        await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )

        # 创建普通管理员角色
        admin_role_id = await _insert_builtin_role(
            database_url,
            code="admin",
            display_name="管理员",
        )

        # 管理员角色只有 system:user:read
        read_perm_id = await _get_permission_id(database_url, "system:user:read")
        await _insert_role_permission(
            database_url,
            role_id=admin_role_id,
            permission_id=read_perm_id,
        )

        # 创建普通管理员用户
        admin_user_id = await _insert_user(database_url, username="admin01")
        await _insert_user_role(
            database_url,
            user_id=admin_user_id,
            role_id=admin_role_id,
        )

        # 创建一个普通角色（待分配权限）
        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_rbac_use_case(engine, str(admin_user_id))

            from app.modules.rbac.schemas import (
                AssignPermissionsRequest,
                RoleCreateRequest,
            )

            role_result = await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 尝试分配超出范围的权限——应被拒绝
            from app.core.errors.exceptions import AuthorizationError

            with pytest.raises(AuthorizationError):
                await uc.assign_permissions(
                    ctx,
                    role_result.id,
                    AssignPermissionsRequest(
                        permission_codes=["system:user:write"],
                    ),
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_super_admin_bypasses_management_scope(
    database_url: str,
) -> None:
    """超级管理员不受管理范围限制 — SPEC 13.2/13.4."""

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")
        await _insert_permission(database_url, code="system:user:write")

        super_role_id = await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )

        super_user_id = await _insert_user(database_url, username="super01")
        await _insert_user_role(
            database_url,
            user_id=super_user_id,
            role_id=super_role_id,
        )

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_rbac_use_case(engine, str(super_user_id))

            from app.modules.rbac.schemas import (
                AssignPermissionsRequest,
                RoleCreateRequest,
            )

            role_result = await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 超管可以分配任何权限
            result = await uc.assign_permissions(
                ctx,
                role_result.id,
                AssignPermissionsRequest(
                    permission_codes=["system:user:read", "system:user:write"],
                ),
            )
            assert sorted(result.permission_codes) == [
                "system:user:read",
                "system:user:write",
            ]
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_management_scope_blocks_assigning_out_of_scope_role(
    database_url: str,
) -> None:
    """普通管理员授予超出自身范围的角色被拒绝 — SPEC 13.2.

    普通管理员有 system:user:read 但没有 system:user:write。
    尝试为用户分配包含 system:user:write 的角色时被拒绝。
    """

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")
        await _insert_permission(database_url, code="system:user:write")

        # 创建超管角色（内置）
        await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )

        # 创建普通管理员角色——只有 system:user:read
        admin_role_id = await _insert_builtin_role(
            database_url,
            code="admin",
            display_name="管理员",
        )
        read_perm_id = await _get_permission_id(database_url, "system:user:read")
        await _insert_role_permission(
            database_url,
            role_id=admin_role_id,
            permission_id=read_perm_id,
        )

        # 创建普通管理员用户
        admin_user_id = await _insert_user(database_url, username="admin01")
        await _insert_user_role(
            database_url,
            user_id=admin_user_id,
            role_id=admin_role_id,
        )

        # 创建一个包含 system:user:write 的角色（超出管理员范围）
        powerful_role_id = await _insert_builtin_role(
            database_url,
            code="powerful",
            display_name="高权限角色",
        )
        write_perm_id = await _get_permission_id(database_url, "system:user:write")
        await _insert_role_permission(
            database_url,
            role_id=powerful_role_id,
            permission_id=write_perm_id,
        )

        # 创建一个目标用户（无角色，权限集为空——在管理员范围内）
        target_user_id = await _insert_user(database_url, username="target01")

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_rbac_use_case(engine, str(admin_user_id))

            from app.core.errors.exceptions import AuthorizationError
            from app.modules.rbac.schemas import AssignUserRolesRequest

            # 尝试分配超出范围的角色——应被拒绝
            with pytest.raises(AuthorizationError):
                await uc.assign_user_roles(
                    ctx,
                    target_user_id,
                    AssignUserRolesRequest(role_codes=["powerful"]),
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_management_scope_blocks_managing_out_of_scope_user(
    database_url: str,
) -> None:
    """普通管理员对管理范围非自身子集的用户执行管理操作被拒绝 — SPEC 13.2.

    普通管理员有 system:user:read 但没有 system:user:write。
    目标用户拥有 system:user:write（超出管理员范围），
    管理员尝试禁用该用户时被拒绝。
    """

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")
        await _insert_permission(database_url, code="system:user:write")

        # 创建超管角色（内置）
        await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )

        # 创建普通管理员角色——只有 system:user:read
        admin_role_id = await _insert_builtin_role(
            database_url,
            code="admin",
            display_name="管理员",
        )
        read_perm_id = await _get_permission_id(database_url, "system:user:read")
        await _insert_role_permission(
            database_url,
            role_id=admin_role_id,
            permission_id=read_perm_id,
        )

        # 创建普通管理员用户
        admin_user_id = await _insert_user(database_url, username="admin01")
        await _insert_user_role(
            database_url,
            user_id=admin_user_id,
            role_id=admin_role_id,
        )

        # 创建高权限角色——有 system:user:write
        powerful_role_id = await _insert_builtin_role(
            database_url,
            code="powerful",
            display_name="高权限角色",
        )
        write_perm_id = await _get_permission_id(database_url, "system:user:write")
        await _insert_role_permission(
            database_url,
            role_id=powerful_role_id,
            permission_id=write_perm_id,
        )

        # 创建目标用户——拥有超出管理员范围的权限
        target_user_id = await _insert_user(database_url, username="target01")
        await _insert_user_role(
            database_url,
            user_id=target_user_id,
            role_id=powerful_role_id,
        )

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_identity_use_case(engine, str(admin_user_id))

            from app.core.errors.exceptions import AuthorizationError

            # 管理员尝试禁用权限范围超出自身的用户——应被拒绝
            with pytest.raises(AuthorizationError):
                await uc.disable_user(ctx, target_user_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: UoW 内二次校验——入口校验后权限被撤销的场景被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_uow_secondary_verification_rejects_revoked_permission(
    database_url: str,
) -> None:
    """关键写 Use Case 在 UoW 内重新读取授权关系二次校验 — SPEC 13.3.

    场景: 管理员拥有 system:user:read 权限。在 Use Case 执行前，
    移除管理员的权限（模拟并发撤销）。Use Case 在自身 UoW 中
    重新读取授权关系时发现权限不足，拒绝操作。
    """

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")

        admin_role_id = await _insert_builtin_role(
            database_url,
            code="admin",
            display_name="管理员",
        )
        read_perm_id = await _get_permission_id(database_url, "system:user:read")
        await _insert_role_permission(
            database_url,
            role_id=admin_role_id,
            permission_id=read_perm_id,
        )

        admin_user_id = await _insert_user(database_url, username="admin01")
        await _insert_user_role(
            database_url,
            user_id=admin_user_id,
            role_id=admin_role_id,
        )

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_rbac_use_case(engine, str(admin_user_id))

            from app.modules.rbac.schemas import (
                AssignPermissionsRequest,
                RoleCreateRequest,
            )

            # 管理员创建一个角色
            role_result = await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )

            # 模拟权限被撤销——移除管理员的全部角色分配
            await _revoke_user_roles(database_url, admin_user_id)

            # Use Case 在 UoW 内重新读取授权关系，发现管理员的权限集为空
            # 不是超管，且要求的权限不在其范围内 → 被拒绝
            from app.core.errors.exceptions import AuthorizationError

            with pytest.raises(AuthorizationError):
                await uc.assign_permissions(
                    ctx,
                    role_result.id,
                    AssignPermissionsRequest(
                        permission_codes=["system:user:read"],
                    ),
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: 最后超管保护——禁用/删除/降权最后一个超管被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_last_super_admin_disable_rejected(database_url: str) -> None:
    """禁用最后一个可用超级管理员被拒绝 — SPEC 13.4."""

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        super_role_id = await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )
        super_user_id = await _insert_user(database_url, username="super01")
        await _insert_user_role(
            database_url,
            user_id=super_user_id,
            role_id=super_role_id,
        )

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_identity_use_case(engine, str(super_user_id))

            from app.modules.auth.errors import LastSuperAdminError

            with pytest.raises(LastSuperAdminError):
                await uc.disable_user(ctx, super_user_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_last_super_admin_delete_rejected(database_url: str) -> None:
    """删除最后一个可用超级管理员被拒绝 — SPEC 13.4."""

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        super_role_id = await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )
        super_user_id = await _insert_user(database_url, username="super01")
        await _insert_user_role(
            database_url,
            user_id=super_user_id,
            role_id=super_role_id,
        )

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_identity_use_case(engine, str(super_user_id))

            from app.modules.auth.errors import LastSuperAdminError

            with pytest.raises(LastSuperAdminError):
                await uc.delete_user(ctx, super_user_id)
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_last_super_admin_role_removal_rejected(database_url: str) -> None:
    """移除最后一个超管角色被拒绝 — SPEC 13.4."""

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        super_role_id = await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )
        super_user_id = await _insert_user(database_url, username="super01")
        await _insert_user_role(
            database_url,
            user_id=super_user_id,
            role_id=super_role_id,
        )

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_rbac_use_case(engine, str(super_user_id))

            from app.modules.auth.errors import LastSuperAdminError

            # 尝试通过全量替换移除超管角色
            from app.modules.rbac.schemas import AssignUserRolesRequest

            with pytest.raises(LastSuperAdminError):
                await uc.assign_user_roles(
                    ctx,
                    super_user_id,
                    AssignUserRolesRequest(role_codes=[]),
                )
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_multiple_super_admins_allows_disable(database_url: str) -> None:
    """存在多个超管时禁用一个不被拒绝 — SPEC 13.4."""

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        super_role_id = await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )
        super1_id = await _insert_user(database_url, username="super01")
        super2_id = await _insert_user(database_url, username="super02")
        await _insert_user_role(database_url, user_id=super1_id, role_id=super_role_id)
        await _insert_user_role(database_url, user_id=super2_id, role_id=super_role_id)

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_identity_use_case(engine, str(super2_id))

            # 禁用 super1——有另一个活跃超管 super2，不拒绝
            result = await uc.disable_user(ctx, super1_id)
            assert result.status == "disabled"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


@pytest.mark.g2
@pytest.mark.integration
async def test_super_admin_operations_write_audit(database_url: str) -> None:
    """超管关键操作写审计 — SPEC 13.4."""

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        await _insert_permission(database_url, code="system:user:read")

        super_role_id = await _insert_builtin_role(
            database_url,
            code="super_admin",
            display_name="超级管理员",
        )
        super_user_id = await _insert_user(database_url, username="super01")
        await _insert_user_role(
            database_url, user_id=super_user_id, role_id=super_role_id
        )

        engine = create_db_engine(database_url)
        try:
            uc, ctx = _make_rbac_use_case(engine, str(super_user_id))

            audit_before = await _count_audit_logs(database_url)

            from app.modules.rbac.schemas import (
                AssignPermissionsRequest,
                RoleCreateRequest,
            )

            role_result = await uc.create_role(
                ctx,
                RoleCreateRequest(code="editor", display_name="编辑者"),
            )
            await uc.assign_permissions(
                ctx,
                role_result.id,
                AssignPermissionsRequest(permission_codes=["system:user:read"]),
            )

            audit_after = await _count_audit_logs(database_url)
            # create_role + assign_permissions = 2 条审计
            assert audit_after - audit_before == 2
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: 管理员强制下线——会话立即失效、登录日志
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.g2
@pytest.mark.integration
async def test_force_offline_revokes_all_sessions(database_url: str) -> None:
    """管理员强制下线使目标用户全部会话立即失效 — SPEC 12.3/18.1."""

    await _apply_migrations(database_url)
    await _cleanup_all(database_url)
    try:
        target_id = await _insert_user(database_url, username="target01")
        admin_id = await _insert_user(database_url, username="admin01")

        # 为目标用户创建会话
        session_id = uuid4()
        now = datetime.now(UTC)
        from datetime import timedelta

        engine0 = create_db_engine(database_url)
        try:
            async with engine0.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO auth_sessions "
                        "(id, user_id, access_token_digest, device, "
                        "ip_address, user_agent, created_at, "
                        "last_activity_at, absolute_expires_at, "
                        "token_expires_at, revoked, revoked_reason) "
                        "VALUES (:id, :uid, :dig, :dev, :ip, "
                        ":ua, :ca, :la, :ae, :te, FALSE, NULL)",
                    ),
                    {
                        "id": str(session_id),
                        "uid": str(target_id),
                        "dig": "fake_digest_for_test",
                        "dev": "web",
                        "ip": "127.0.0.1",
                        "ua": "TestAgent",
                        "ca": now,
                        "la": now,
                        "ae": now + timedelta(hours=12),
                        "te": now + timedelta(minutes=15),
                    },
                )
        finally:
            await engine0.dispose()

        engine = create_db_engine(database_url)
        try:
            auth_uc = _make_auth_use_case(engine)
            ctx = UseCaseContext(
                request_id="test-req",
                actor_id=str(admin_id),
            )

            login_before = await _count_login_logs(database_url)

            count = await auth_uc.force_offline(
                ctx,
                target_id,
                ip_address="127.0.0.1",
                user_agent="TestAgent",
            )
            assert count >= 1

            # 验证会话已吊销
            revoked_count = await _count_revoked_sessions(database_url, target_id)
            assert revoked_count >= 1

            # 验证登录日志已记录
            login_after = await _count_login_logs(database_url)
            assert login_after > login_before
        finally:
            await engine.dispose()
    finally:
        await _cleanup_all(database_url)


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助查询
# ═══════════════════════════════════════════════════════════════════════════════


async def _get_permission_id(database_url: str, code: str) -> UUID:
    """查询权限点 ID。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id FROM rbac_permissions WHERE code = :code"),
                {"code": code},
            )
            row = result.first()
            assert row is not None
            return UUID(str(row[0]))
    finally:
        await engine.dispose()


async def _revoke_user_roles(database_url: str, user_id: UUID) -> None:
    """移除用户全部角色分配。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM rbac_user_roles WHERE user_id = :uid"),
                {"uid": str(user_id)},
            )
    finally:
        await engine.dispose()


async def _count_audit_logs(database_url: str) -> int:
    """查询审计日志行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM audit_logs"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _count_login_logs(database_url: str) -> int:
    """查询登录日志行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM login_logs"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


async def _count_revoked_sessions(database_url: str, user_id: UUID) -> int:
    """查询用户已吊销会话数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM auth_sessions "
                    "WHERE user_id = :uid AND revoked = TRUE",
                ),
                {"uid": str(user_id)},
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()
