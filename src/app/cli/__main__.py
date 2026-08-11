"""管理命令行入口 — argparse 实现.

SPEC 25.1:
  - ``uv run python -m app.cli config show`` 只输出脱敏后的运行配置摘要。
  - ``uv run python -m app.cli db check`` 检查数据库连接并返回明确退出码。
  - ``uv run python -m app.cli db upgrade`` 只执行 ``alembic upgrade head``，
    不得隐式创建管理员或业务数据。
  - 所有命令成功返回 0，参数错误返回 2，配置或运行失败返回非 0。
  - 命令失败时不得吞掉异常或留下半完成的隐式状态。

退出码语义（SPEC 25.1）:
  - 0: 命令成功。
  - 2: 参数错误（argparse 自动处理）。
  - 非 0: 配置或运行失败。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

from app.core.config import Settings, mask_url_password

# Windows 的 ProactorEventLoop 与 psycopg3 异步模式不兼容。
# CLI 命令中使用 asyncio.run 前切换为 SelectorEventLoop。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import SecretStr


def _create_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器.

    使用 argparse 标准库实现子命令结构。
    非法参数由 argparse 自动处理并退出码 2（SPEC 25.1）。
    """

    parser = argparse.ArgumentParser(
        prog="app.cli",
        description="Apex Admin 管理命令行工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # config 子命令
    config_parser = subparsers.add_parser(
        "config",
        help="配置管理",
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser(
        "show",
        help="输出脱敏后的运行配置摘要",
    )

    # db 子命令
    db_parser = subparsers.add_parser(
        "db",
        help="数据库管理",
    )
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser(
        "check",
        help="检查数据库连接并返回退出码",
    )
    db_sub.add_parser(
        "upgrade",
        help="执行 alembic upgrade head（不创建业务数据）",
    )

    # modules 子命令 — SPEC 25.1
    modules_parser = subparsers.add_parser(
        "modules",
        help="模块管理",
    )
    modules_sub = modules_parser.add_subparsers(
        dest="modules_command",
        required=True,
    )
    modules_sub.add_parser(
        "validate",
        help="验证模块编码、路由、权限点、错误码、事件和 Alembic 单 head",
    )

    # auth 子命令 — SPEC 25.2
    auth_parser = subparsers.add_parser(
        "auth",
        help="身份与权限管理",
    )
    auth_sub = auth_parser.add_subparsers(
        dest="auth_command",
        required=True,
    )
    sync_parser = auth_sub.add_parser(
        "sync-permissions",
        help="幂等同步 G2 启用模块声明的权限点到权限目录",
    )
    sync_parser.add_argument(
        "--clean-orphans",
        action="store_true",
        default=False,
        help="清理孤立权限点（代码中已移除但仍存在于数据库的权限点）",
    )
    sync_parser.add_argument(
        "--confirm",
        action="store_true",
        default=False,
        help="确认执行破坏性操作（与 --clean-orphans 配合使用）",
    )

    # auth create-admin — SPEC 25.2
    create_admin_parser = auth_sub.add_parser(
        "create-admin",
        help="安全创建首个管理员（密码经标准输入传入）",
    )
    create_admin_parser.add_argument(
        "--username",
        required=True,
        help="管理员用户名（幂等自然键）",
    )

    # auth rotate-token-keys — SPEC 23.2
    auth_sub.add_parser(
        "rotate-token-keys",
        help="生成 Token HMAC 密钥轮换配置（双密钥短期切换）",
    )

    # dev 子命令 — 开发演示数据（SPEC 8.5）
    dev_parser = subparsers.add_parser(
        "dev",
        help="开发工具（仅限开发/测试环境）",
    )
    dev_sub = dev_parser.add_subparsers(
        dest="dev_command",
        required=True,
    )
    dev_sub.add_parser(
        "seed-demo",
        help="创建开发演示数据（仅限非生产环境）",
    )

    # sysconfig 子命令 — SPEC 16.1 / 23.2
    sysconfig_parser = subparsers.add_parser(
        "sysconfig",
        help="系统配置管理",
    )
    sysconfig_sub = sysconfig_parser.add_subparsers(
        dest="sysconfig_command",
        required=True,
    )
    sysconfig_sub.add_parser(
        "re-encrypt",
        help="敏感配置加密密钥轮换重加密（SPEC 23.2 双密钥短期切换）",
    )

    # audit 子命令 — SPEC 18.4 / 25.3
    audit_parser = subparsers.add_parser(
        "audit",
        help="审计日志管理",
    )
    audit_sub = audit_parser.add_subparsers(
        dest="audit_command",
        required=True,
    )
    audit_cleanup_parser = audit_sub.add_parser(
        "cleanup",
        help="审计日志保留清理（默认 dry-run）",
    )
    audit_cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="实际执行删除（默认 dry-run 只报告）",
    )

    return parser


def _format_config_summary(settings: Settings) -> str:
    """生成脱敏后的配置摘要文本.

    SPEC 25.1: 只输出脱敏后的运行配置摘要。
    - SecretStr 字段显示为掩码。
    - DATABASE_URL 中的密码被脱敏。
    - 其他字段原样显示。
    """

    lines: list[str] = [
        "应用配置摘要（已脱敏）",
        "=" * 50,
    ]

    # 基本信息字段
    lines.append(f"  APP_NAME:               {settings.APP_NAME}")
    lines.append(f"  APP_VERSION:            {settings.APP_VERSION}")
    lines.append(f"  ENVIRONMENT:            {settings.ENVIRONMENT.value}")
    lines.append(f"  API_PREFIX:             {settings.API_PREFIX}")

    # 数据库 URL — 脱敏密码部分
    db_url = mask_url_password(settings.DATABASE_URL)
    lines.append(f"  DATABASE_URL:           {db_url}")
    lines.append(f"  DB_POOL_SIZE:           {settings.DB_POOL_SIZE}")
    lines.append(f"  DB_MAX_OVERFLOW:        {settings.DB_MAX_OVERFLOW}")

    # 密钥字段 — 全部掩码
    access_masked = _mask_secret(settings.ACCESS_TOKEN_HMAC_KEY)
    lines.append(f"  ACCESS_TOKEN_HMAC_KEY:  {access_masked}")
    refresh_masked = _mask_secret(settings.REFRESH_TOKEN_HMAC_KEY)
    lines.append(f"  REFRESH_TOKEN_HMAC_KEY: {refresh_masked}")

    lines.append(f"  LOG_LEVEL:              {settings.LOG_LEVEL}")
    lines.append("=" * 50)

    return "\n".join(lines)


def _mask_secret(secret: SecretStr | None) -> str:
    """将 SecretStr 掩码为固定占位符."""

    if secret is None:
        return "<未设置>"
    return "**********"


def _cmd_config_show() -> int:
    """执行 config show 命令 — 输出脱敏配置摘要.

    SPEC 25.1: 成功返回 0，配置失败返回非 0。
    Settings 构造时的校验异常（如生产环境密钥缺失）会导致非 0 退出。
    """

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    print(_format_config_summary(settings))
    return 0


async def _check_db_connection(database_url: str) -> bool:
    """异步检查数据库连通性.

    使用 ``SELECT 1`` 验证连接可用，连接失败返回 False。
    """

    from sqlalchemy import text

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


def _cmd_db_check() -> int:
    """执行 db check 命令 — 检查数据库连接.

    SPEC 25.1:
      - 库可用返回 0。
      - 断库返回非 0。
      - 命令失败时不得吞掉异常或留下半完成的隐式状态。
    """

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    try:
        ok = asyncio.run(_check_db_connection(settings.DATABASE_URL))
    except Exception as exc:
        print(f"数据库检查失败: {exc}", file=sys.stderr)
        return 1

    if ok:
        print("数据库连接正常")
        return 0

    print("数据库连接失败", file=sys.stderr)
    return 1


def _cmd_modules_validate() -> int:
    """执行 modules validate 命令 — SPEC 25.1.

    SPEC 25.1: ``uv run python -m app.cli modules validate`` 验证模块编码、
    路由、权限点、错误码、事件和 Alembic 单 head。

    SPEC 34.1: 返回 0 并报告零重复模块、路由、权限点、错误码、事件和命令。

    校验内容:
      1. 模块声明无重复（编码、Tag、权限点、错误码等）。
      2. 依赖关系正确（必需依赖存在、无循环依赖）。
      3. Alembic 只有一个 head revision。

    退出码:
      0: 校验通过，零重复。
      非 0: 校验失败或运行错误。
    """

    from app.composition.modules import get_module_manifest
    from app.core.modules.exceptions import ModuleValidationError
    from app.core.modules.registry import ModuleRegistry

    manifest = get_module_manifest()
    registry = ModuleRegistry.from_modules(manifest)

    # ── 模块声明与依赖校验 ──
    try:
        registry.validate_or_raise()
    except ModuleValidationError as exc:
        print(f"模块校验失败: {exc}", file=sys.stderr)
        return 1

    # ── 报告零重复 ──
    module_count = len(manifest)
    print(f"模块校验通过: {module_count} 个模块已注册，零重复声明")

    # ── Alembic 单 head 校验 ──
    try:
        from app.composition.modules import MODULE_VERSION_LOCATIONS
        from app.infrastructure.db.migrations import get_head_revision

        get_head_revision(MODULE_VERSION_LOCATIONS)
        print("Alembic 单 head 校验通过")
    except RuntimeError as exc:
        print(f"Alembic 校验失败: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Alembic 校验出错: {exc}", file=sys.stderr)
        return 1

    return 0


def _cmd_db_upgrade() -> int:
    """执行 db upgrade 命令 — 仅执行 alembic upgrade head.

    SPEC 25.1:
      - 只执行 ``alembic upgrade head``。
      - 不得隐式创建管理员或业务数据。
    """

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    try:
        from alembic import command
        from alembic.util.exc import CommandError

        from app.composition.modules import MODULE_VERSION_LOCATIONS
        from app.infrastructure.db.migrations import get_alembic_config

        config = get_alembic_config(
            settings.DATABASE_URL,
            version_locations=MODULE_VERSION_LOCATIONS,
        )
        command.upgrade(config, "head")
    except CommandError as exc:
        print(f"数据库迁移失败: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"数据库迁移失败: {exc}", file=sys.stderr)
        return 1

    print("数据库迁移已执行至 head")
    return 0


async def _run_sync_permissions(
    database_url: str,
    *,
    clean_orphans: bool,
) -> int:
    """异步执行权限点目录同步 — SPEC 25.2."""

    from app.infrastructure.db.engine import create_db_engine
    from app.modules.rbac.sync import collect_declared_permissions, sync_permissions

    engine = create_db_engine(database_url)
    try:
        declared = collect_declared_permissions()
        result = await sync_permissions(
            engine,
            declared_permissions=declared,
            clean_orphans=clean_orphans,
        )

        # SPEC 25.2: "权限同步默认只新增和更新，不自动删除"
        if result.added:
            print(f"新增权限点 ({len(result.added)}):")
            for code in result.added:
                print(f"  + {code}")

        if result.updated:
            print(f"更新权限点 ({len(result.updated)}):")
            for code in result.updated:
                print(f"  ~ {code}")

        if result.orphaned:
            print(
                f"孤立权限点 ({len(result.orphaned)}) — 代码中已移除但仍存在于数据库:",
            )
            for code in result.orphaned:
                print(f"  ! {code}")

        if result.cleaned:
            print(f"已清理孤立权限点 ({len(result.cleaned)}):")
            for code in result.cleaned:
                print(f"  - {code}")

        if not result.added and not result.updated and not result.cleaned:
            print(
                f"权限同步完成，无新增或更新（共 {result.total_in_db} 个权限点）",
            )
        else:
            print(
                f"权限同步完成: 新增 {len(result.added)}，"
                f"更新 {len(result.updated)}，"
                f"孤立 {len(result.orphaned)}，"
                f"清理 {len(result.cleaned)}，"
                f"总计 {result.total_in_db}",
            )

        return 0
    finally:
        await engine.dispose()


def _cmd_auth_sync_permissions(args: argparse.Namespace) -> int:
    """执行 auth sync-permissions 命令 — SPEC 25.2.

    SPEC 25.2:
      - 幂等同步 G2 启用模块声明的权限点。
      - 权限同步默认只新增和更新，不自动删除。
      - 孤立权限点必须报告并由显式确认命令清理。

    SPEC 25.3: "所有修复命令默认 dry-run；实际修改必须使用显式 ``--apply``"。
    ``--clean-orphans`` 要求同时提供 ``--confirm`` 标志。
    """

    clean_orphans = getattr(args, "clean_orphans", False)
    confirm = getattr(args, "confirm", False)

    if clean_orphans and not confirm:
        print(
            "清理孤立权限点需要同时使用 --confirm 标志确认",
            file=sys.stderr,
        )
        return 1

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    try:
        return asyncio.run(
            _run_sync_permissions(
                settings.DATABASE_URL,
                clean_orphans=clean_orphans,
            ),
        )
    except Exception as exc:
        print(f"权限同步失败: {exc}", file=sys.stderr)
        return 1


# ── auth create-admin — SPEC 25.2 ─────────────────────────────────────────


def _read_password_from_stdin() -> str:
    """从受控标准输入读取密码 — SPEC 25.2 / 23.2.

    SPEC 25.2: "管理员初始密码只能通过交互式隐藏输入或受控标准输入传入，
    不允许作为命令行参数"。
    SPEC 23.2: "禁止记录和回显密码"。

    当标准输入为 TTY 时使用 getpass 隐藏输入；管道输入时直接读取一行。
    密码不在任何地方被打印或记录。
    """

    import getpass

    if sys.stdin.isatty():
        password = getpass.getpass("请输入管理员密码: ")
    else:
        # 管道/重定向模式 — 从标准输入读取一行
        line = sys.stdin.readline()
        password = line.rstrip("\n").rstrip("\r")

    return password


async def _run_create_admin(
    database_url: str,
    *,
    username: str,
    password: str,
) -> int:
    """异步执行 create-admin — 创建管理员并分配超管角色.

    SPEC 25.2 / 8.5:
      - 创建管理员用户，密码使用 Argon2id 哈希。
      - 分配内置 super_admin 角色。
      - 幂等：同名管理员已存在时报告成功，不创建重复记录。
      - 密码不写入日志、不输出到命令行。
    """

    from app.application.ports import SystemClock, UuidGenerator
    from app.core.security.authorization import SUPER_ADMIN_ROLE_CODE
    from app.core.security.password import (
        Argon2Hasher,
        PasswordPolicyError,
        validate_password_length,
    )
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.identity.adapter import SqlAlchemyUserRepository
    from app.modules.identity.models import User, UserStatus
    from app.modules.rbac.adapter import SqlAlchemyRbacRepository
    from app.modules.rbac.initializers import BuiltinRolesInitializer

    # SPEC 23.2: 校验密码长度策略
    try:
        validate_password_length(password)
    except PasswordPolicyError as exc:
        print(f"密码策略校验失败: {exc}", file=sys.stderr)
        return 1

    hasher = Argon2Hasher()
    password_hash = hasher.hash(password)
    clock = SystemClock()
    id_gen = UuidGenerator()
    now = clock.now()

    engine = create_db_engine(database_url)
    try:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            user_repo = SqlAlchemyUserRepository(uow.session)
            rbac_repo = SqlAlchemyRbacRepository(uow.session)

            # 1. 幂等检查 — 同名管理员已存在则报告成功
            existing = await user_repo.get_by_username(username)
            if existing is not None:
                print(f"管理员 '{username}' 已存在，跳过创建（幂等）")
                await uow.commit()
                return 0

            # 2. 确保内置角色存在（运行初始化器）
            initializer = BuiltinRolesInitializer()
            await initializer.initialize(uow.session)

            # 3. 创建管理员用户
            user = User(
                id=id_gen.generate_id(),
                username=username,
                display_name=username,
                password_hash=password_hash,
                status=UserStatus.ACTIVE,
                phone=None,
                email=None,
                last_login_at=None,
                password_updated_at=now,
                created_at=now,
                updated_at=now,
                created_by="cli:create-admin",
                updated_by="cli:create-admin",
            )
            await user_repo.add(user)

            # 4. 查找 super_admin 角色并分配
            roles = await rbac_repo.get_roles_by_codes({SUPER_ADMIN_ROLE_CODE})
            if not roles:
                print(
                    f"内置角色 '{SUPER_ADMIN_ROLE_CODE}' 不存在",
                    file=sys.stderr,
                )
                return 1
            super_admin_role = roles[0]
            await rbac_repo.add_user_role(
                user.id,
                super_admin_role.id,
                now=now,
                created_by="cli:create-admin",
            )

            await uow.commit()

        print(f"管理员 '{username}' 创建成功，已分配超级管理员角色")
        return 0
    finally:
        await engine.dispose()


def _cmd_auth_create_admin(args: argparse.Namespace) -> int:
    """执行 auth create-admin 命令 — SPEC 25.2.

    SPEC 25.2:
      - 安全创建首个管理员。
      - 管理员初始密码只能通过交互式隐藏输入或受控标准输入传入。
      - 密码不写入日志、不输出到命令行。
      - 幂等：同名管理员已存在时报告成功，不创建重复记录。
    """

    username = args.username

    password = _read_password_from_stdin()
    if not password:
        print("密码不能为空", file=sys.stderr)
        return 1

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    try:
        return asyncio.run(
            _run_create_admin(
                settings.DATABASE_URL,
                username=username,
                password=password,
            ),
        )
    except Exception as exc:
        # SPEC 23.2: 异常信息中不得包含密码
        print(f"创建管理员失败: {exc}", file=sys.stderr)
        return 1


# ── auth rotate-token-keys — SPEC 23.2 ────────────────────────────────────


def _cmd_auth_rotate_token_keys() -> int:
    """执行 auth rotate-token-keys 命令 — SPEC 23.2.

    SPEC 23.2: "密钥轮换必须具有独立管理命令和双密钥短期切换步骤，
    不得通过永久 fallback 兼容旧密钥"。

    生成新的 CSPRNG 密钥并输出轮换配置说明。
    当前密钥值不从配置中读取或输出——操作者自行将当前环境变量复制到 _PREVIOUS。
    """

    from datetime import UTC, datetime, timedelta

    from app.core.security.token import generate_token

    # SPEC 12.2: 密钥至少 256 bit 熵 = 32 字节
    # 使用 generate_token() 生成 URL-safe base64 编码的随机串
    new_access_key = generate_token()
    new_refresh_key = generate_token()

    expires_at = datetime.now(UTC) + timedelta(hours=24)

    print("=" * 60)
    print("Token HMAC 密钥轮换 — SPEC 23.2")
    print("=" * 60)
    print()
    print("请按以下步骤完成双密钥短期切换：")
    print()
    print("1. 将当前密钥复制到 _PREVIOUS 环境变量（操作者自行完成）：")
    print("   APEX_ACCESS_TOKEN_HMAC_KEY_PREVIOUS=<当前 ACCESS_TOKEN_HMAC_KEY>")
    print("   APEX_REFRESH_TOKEN_HMAC_KEY_PREVIOUS=<当前 REFRESH_TOKEN_HMAC_KEY>")
    print()
    print("2. 设置新密钥：")
    print(f"   APEX_ACCESS_TOKEN_HMAC_KEY={new_access_key}")
    print(f"   APEX_REFRESH_TOKEN_HMAC_KEY={new_refresh_key}")
    print()
    print("3. 设置轮换窗口过期时间（UTC ISO 8601）：")
    print(f"   APEX_KEY_ROTATION_EXPIRES_AT={expires_at.isoformat()}")
    print()
    print("4. 重启应用 — 轮换窗口内新旧密钥均可验证 Token")
    print()
    print("5. 窗口过期后，移除 _PREVIOUS 和 _EXPIRES_AT 环境变量并重启")
    print("   旧密钥在窗口过期后自动失效，不存在永久 fallback（SPEC 23.2）")
    print()
    print("=" * 60)

    return 0


# ── dev seed-demo — SPEC 8.5 ──────────────────────────────────────────────


async def _run_dev_seed_demo(database_url: str) -> int:
    """异步执行 dev seed-demo — 创建开发演示数据.

    SPEC 8.5: "开发演示数据和生产初始化数据使用不同命令与数据源"。
    此命令仅在非生产环境使用，创建少量演示数据用于本地开发。
    """

    from app.application.ports import SystemClock, UuidGenerator
    from app.core.security.authorization import SUPER_ADMIN_ROLE_CODE
    from app.core.security.password import Argon2Hasher
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.identity.adapter import SqlAlchemyUserRepository
    from app.modules.identity.models import User, UserStatus
    from app.modules.rbac.adapter import SqlAlchemyRbacRepository
    from app.modules.rbac.initializers import BuiltinRolesInitializer
    from app.modules.rbac.sync import collect_declared_permissions, sync_permissions

    hasher = Argon2Hasher()
    clock = SystemClock()
    id_gen = UuidGenerator()
    now = clock.now()

    # 开发演示密码（不用于生产）
    demo_password = "demo-admin-password-123"
    password_hash = hasher.hash(demo_password)

    engine = create_db_engine(database_url)
    try:
        # 1. 同步权限点目录
        declared = collect_declared_permissions()
        await sync_permissions(engine, declared_permissions=declared)

        # 2. 创建演示管理员
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            user_repo = SqlAlchemyUserRepository(uow.session)
            rbac_repo = SqlAlchemyRbacRepository(uow.session)

            # 确保内置角色存在
            initializer = BuiltinRolesInitializer()
            await initializer.initialize(uow.session)

            existing = await user_repo.get_by_username("demo-admin")
            if existing is None:
                demo_user = User(
                    id=id_gen.generate_id(),
                    username="demo-admin",
                    display_name="演示管理员",
                    password_hash=password_hash,
                    status=UserStatus.ACTIVE,
                    phone=None,
                    email=None,
                    last_login_at=None,
                    password_updated_at=now,
                    created_at=now,
                    updated_at=now,
                    created_by="cli:dev:seed-demo",
                    updated_by="cli:dev:seed-demo",
                )
                await user_repo.add(demo_user)

                roles = await rbac_repo.get_roles_by_codes({SUPER_ADMIN_ROLE_CODE})
                if roles:
                    await rbac_repo.add_user_role(
                        demo_user.id,
                        roles[0].id,
                        now=now,
                        created_by="cli:dev:seed-demo",
                    )

            await uow.commit()

        print("开发演示数据已创建:")
        print("  演示管理员: demo-admin")
        print(f"  演示密码: {demo_password}")
        print("  权限点目录: 已同步")
        print()
        print("警告: 此命令仅限开发/测试环境使用，生产环境禁止运行（SPEC 8.5）")
        return 0
    finally:
        await engine.dispose()


def _cmd_dev_seed_demo() -> int:
    """执行 dev seed-demo 命令 — SPEC 8.5.

    SPEC 8.5: "开发演示数据和生产初始化数据使用不同命令与数据源"。
    此命令仅在非生产环境允许执行。
    """

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    if settings.ENVIRONMENT.value == "production":
        print(
            "dev seed-demo 禁止在生产环境执行（SPEC 8.5）",
            file=sys.stderr,
        )
        return 1

    try:
        return asyncio.run(_run_dev_seed_demo(settings.DATABASE_URL))
    except Exception as exc:
        print(f"开发演示数据创建失败: {exc}", file=sys.stderr)
        return 1


# ── sysconfig re-encrypt — SPEC 23.2 ───────────────────────────────────────


async def _run_sysconfig_re_encrypt(
    database_url: str,
    *,
    current_key: str,
    previous_key: str | None,
) -> int:
    """异步执行敏感配置密钥轮换重加密 — SPEC 23.2.

    SPEC 23.2: "密钥轮换必须具有独立管理命令和双密钥短期切换步骤，
    不得通过永久 fallback 兼容旧密钥"。

    流程:
      1. 构造加密服务（当前密钥 + 前一代密钥）。
      2. 查询全部敏感配置项。
      3. 对每个敏感配置项的密文执行 ``rotate``（旧密钥解密 → 当前密钥加密）。
      4. 保存重加密后的密文。
    """

    from app.application.ports import SystemClock
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.sysconfig.adapter import SqlAlchemyConfigRepository
    from app.modules.sysconfig.crypto import ConfigEncryptionService
    from app.modules.sysconfig.models import ConfigItem

    encryption = ConfigEncryptionService(
        current_key=current_key,
        previous_key=previous_key,
    )
    clock = SystemClock()

    engine = create_db_engine(database_url)
    try:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            repo = SqlAlchemyConfigRepository(uow.session)
            sensitive_items = await repo.list_sensitive_items()

            if not sensitive_items:
                print("无敏感配置项需要重加密")
                await uow.commit()
                return 0

            rotated_count = 0
            now = clock.now()
            for item in sensitive_items:
                new_ciphertext = encryption.rotate(item.stored_value)
                updated = ConfigItem(
                    id=item.id,
                    group=item.group,
                    key=item.key,
                    value_type=item.value_type,
                    stored_value=new_ciphertext,
                    is_sensitive=item.is_sensitive,
                    is_core_security=item.is_core_security,
                    description=item.description,
                    status=item.status,
                    created_at=item.created_at,
                    updated_at=now,
                    created_by=item.created_by,
                    updated_by="cli:sysconfig:re-encrypt",
                )
                await repo.save(updated)
                rotated_count += 1

            await uow.commit()

        print(
            f"密钥轮换重加密完成: {rotated_count} 个敏感配置项已使用新密钥重加密",
        )
        return 0
    finally:
        await engine.dispose()


def _cmd_sysconfig_re_encrypt() -> int:
    """执行 sysconfig re-encrypt 命令 — SPEC 23.2.

    SPEC 23.2: "密钥轮换必须具有独立管理命令和双密钥短期切换步骤，
    不得通过永久 fallback 兼容旧密钥"。

    要求 ``SYSCONFIG_ENCRYPTION_KEY_PREVIOUS`` 已设置（前一代密钥），
    用于解密旧密文。重加密后所有密文使用当前密钥。
    """

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    # SPEC 23.2: 当前密钥和前一代密钥都必须由部署配置提供
    assert settings.SYSCONFIG_ENCRYPTION_KEY is not None
    current_key_raw = settings.SYSCONFIG_ENCRYPTION_KEY.get_secret_value()
    previous_key_raw: str | None
    if settings.SYSCONFIG_ENCRYPTION_KEY_PREVIOUS is not None:
        previous_key_raw = settings.SYSCONFIG_ENCRYPTION_KEY_PREVIOUS.get_secret_value()
    else:
        previous_key_raw = None

    if not current_key_raw:
        print(
            "SYSCONFIG_ENCRYPTION_KEY 未设置，无法执行重加密",
            file=sys.stderr,
        )
        return 1

    if not previous_key_raw:
        print(
            "SYSCONFIG_ENCRYPTION_KEY_PREVIOUS 未设置。"
            "请先设置前一代密钥（SPEC 23.2 双密钥短期切换）",
            file=sys.stderr,
        )
        return 1

    try:
        return asyncio.run(
            _run_sysconfig_re_encrypt(
                settings.DATABASE_URL,
                current_key=current_key_raw,
                previous_key=previous_key_raw,
            ),
        )
    except Exception as exc:
        print(f"密钥轮换重加密失败: {exc}", file=sys.stderr)
        return 1


# ── audit cleanup — SPEC 18.4 / 25.3 ──────────────────────────────────────


async def _run_audit_cleanup(
    database_url: str,
    *,
    audit_retention_days: int,
    login_retention_days: int,
    security_retention_days: int,
    apply: bool,
) -> int:
    """异步执行审计日志保留清理 — SPEC 18.4 / 25.3.

    SPEC 25.3: 所有修复命令默认 dry-run；实际修改必须使用显式 ``--apply``
    并记录审计或运维日志。

    SPEC 18.4:
      - 定义审计日志保留期限。
      - 提供受控的归档或清理命令。
      - 清理操作记录执行结果。
      - 安全事件的保留策略独立于普通访问日志。
    """

    from app.application.ports import SystemClock
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.audit.retention import (
        CleanupResult,
        RetentionConfig,
        execute_cleanup,
        format_cleanup_report,
    )

    config = RetentionConfig(
        audit_log_retention_days=audit_retention_days,
        login_log_retention_days=login_retention_days,
        security_event_retention_days=security_retention_days,
    )
    clock = SystemClock()

    engine = create_db_engine(database_url)
    try:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            result: CleanupResult = await execute_cleanup(
                config=config,
                clock=clock,
                apply=apply,
                uow=uow,
            )

            if apply:
                await uow.commit()

        print(format_cleanup_report(result))

        if not apply:
            print(
                "使用 --apply 执行实际删除（SPEC 25.3: 显式 --apply 才修改数据）",
            )
        else:
            print("清理操作已完成并提交")

        return 0
    finally:
        await engine.dispose()


def _cmd_audit_cleanup(args: argparse.Namespace) -> int:
    """执行 audit cleanup 命令 — SPEC 18.4 / 25.3.

    SPEC 25.3: 所有修复命令默认 dry-run；实际修改必须使用显式 ``--apply``。
    SPEC 18.4: 清理操作记录执行结果。
    """

    apply = getattr(args, "apply", False)

    try:
        settings = Settings()
    except Exception as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 1

    try:
        return asyncio.run(
            _run_audit_cleanup(
                settings.DATABASE_URL,
                audit_retention_days=settings.AUDIT_LOG_RETENTION_DAYS,
                login_retention_days=settings.LOGIN_LOG_RETENTION_DAYS,
                security_retention_days=settings.SECURITY_EVENT_RETENTION_DAYS,
                apply=apply,
            ),
        )
    except Exception as exc:
        print(f"审计日志清理失败: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口 — 解析参数并分发到子命令.

    参数:
        argv: 命令行参数列表。None 时使用 ``sys.argv``。

    返回:
        退出码：成功 0，参数错误 2（argparse 自动处理），运行失败非 0。
    """

    parser = _create_parser()
    args = parser.parse_args(argv)

    if args.command == "config" and args.config_command == "show":
        return _cmd_config_show()

    if args.command == "modules" and args.modules_command == "validate":
        return _cmd_modules_validate()

    if args.command == "auth":
        if args.auth_command == "sync-permissions":
            return _cmd_auth_sync_permissions(args)
        if args.auth_command == "create-admin":
            return _cmd_auth_create_admin(args)
        if args.auth_command == "rotate-token-keys":
            return _cmd_auth_rotate_token_keys()

    if args.command == "dev" and args.dev_command == "seed-demo":
        return _cmd_dev_seed_demo()

    if args.command == "sysconfig" and args.sysconfig_command == "re-encrypt":
        return _cmd_sysconfig_re_encrypt()

    if args.command == "audit" and args.audit_command == "cleanup":
        return _cmd_audit_cleanup(args)

    if args.command == "db":
        if args.db_command == "check":
            return _cmd_db_check()
        if args.db_command == "upgrade":
            return _cmd_db_upgrade()

    # 不应到达此处（argparse required=True 已保证子命令存在）
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
