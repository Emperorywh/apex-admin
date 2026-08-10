"""管理命令行入口 — argparse 实现.

SPEC 25.1:
  - ``uv run python -m app.cli config show`` 只输出脱敏后的运行配置摘要。
  - 成功返回 0，参数错误返回 2，配置或运行失败返回非 0。
  - 命令失败时不得吞掉异常或留下半完成的隐式状态。

退出码语义（SPEC 25.1）:
  - 0: 命令成功。
  - 2: 参数错误（argparse 自动处理）。
  - 非 0: 配置或运行失败。
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from app.core.config import Settings, mask_url_password

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

    # 不应到达此处（argparse required=True 已保证子命令存在）
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
