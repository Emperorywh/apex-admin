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

    if args.command == "auth" and args.auth_command == "sync-permissions":
        return _cmd_auth_sync_permissions(args)

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
