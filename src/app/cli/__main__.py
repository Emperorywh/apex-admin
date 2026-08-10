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

        from app.infrastructure.db.migrations import get_alembic_config

        config = get_alembic_config(settings.DATABASE_URL)
        command.upgrade(config, "head")
    except CommandError as exc:
        print(f"数据库迁移失败: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"数据库迁移失败: {exc}", file=sys.stderr)
        return 1

    print("数据库迁移已执行至 head")
    return 0


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
