"""CLI 入口点（SPEC §25.1）。

提供 G1 工程管理命令：

- ``db check``：检查数据库连接
- ``db upgrade``：执行 ``alembic upgrade head``
- ``modules validate``：验证模块注册和 Alembic 单头
- ``config show``：输出脱敏配置摘要

退出码（SPEC §25.1）：
- 成功：0
- 参数错误：2（由 argparse 处理）
- 运行/配置失败：1（由 :func:`main` 捕获异常后返回）

命令失败时打印完整 traceback 到 stderr，不吞掉异常。
"""

from __future__ import annotations

import argparse
import textwrap
import traceback
from collections.abc import Callable, Sequence

from app.cli.config import config_show
from app.cli.db import db_check, db_upgrade
from app.cli.modules import modules_validate


def create_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。

    定义两级子命令结构：
    - 顶层：db / modules / config
    - 二级：check / upgrade / validate / show

    每个二级子命令通过 ``set_defaults(func=...)`` 绑定处理函数。
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Apex Admin 管理命令（SPEC §25.1）",
        epilog=textwrap.dedent("""\
            可用命令：
              db check           检查数据库连接
              db upgrade         执行 alembic upgrade head
              modules validate   验证模块注册和 Alembic 单头
              config show        输出脱敏配置摘要
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # db 子命令
    db_parser = subparsers.add_parser("db", help="数据库管理命令")
    db_subparsers = db_parser.add_subparsers(dest="subcommand", required=True)
    db_check_parser = db_subparsers.add_parser("check", help="检查数据库连接")
    db_check_parser.set_defaults(func=db_check)
    db_upgrade_parser = db_subparsers.add_parser("upgrade", help="执行 alembic upgrade head")
    db_upgrade_parser.set_defaults(func=db_upgrade)

    # modules 子命令
    modules_parser = subparsers.add_parser("modules", help="模块校验命令")
    modules_subparsers = modules_parser.add_subparsers(dest="subcommand", required=True)
    modules_validate_parser = modules_subparsers.add_parser("validate", help="校验模块注册")
    modules_validate_parser.set_defaults(func=modules_validate)

    # config 子命令
    config_parser = subparsers.add_parser("config", help="配置命令")
    config_subparsers = config_parser.add_subparsers(dest="subcommand", required=True)
    config_show_parser = config_subparsers.add_parser("show", help="显示脱敏配置")
    config_show_parser.set_defaults(func=config_show)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口。

    解析参数并分发到对应命令处理器。参数错误由 argparse 处理（退出码 2），
    运行/配置失败由本函数捕获并返回 1，打印完整 traceback（不吞掉异常）。

    Args:
        argv: 命令行参数；为 None 时使用 ``sys.argv[1:]``

    Returns:
        退出码：成功 0，运行/配置失败 1
    """
    parser = create_parser()
    args = parser.parse_args(argv)

    func: Callable[[], int] = args.func
    try:
        result: int = func()
        return result
    except Exception:
        traceback.print_exc()
        return 1
