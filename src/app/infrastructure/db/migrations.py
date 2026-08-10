"""Alembic 迁移工具函数 — SPEC 8.2.

提供 Alembic 配置加载、head revision 查询和迁移执行功能。
供 CLI ``db upgrade`` 命令和健康检查器使用。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

# 项目根目录 — 本文件位于 src/app/infrastructure/db/migrations.py，
# 向上 4 级到达项目根（包含 pyproject.toml 和 alembic.ini 的目录）。
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[4]

#: alembic.ini 文件路径
ALEMBIC_INI_PATH: Path = _PROJECT_ROOT / "alembic.ini"


def get_alembic_config(database_url: str | None = None) -> Config:
    """构造 Alembic 配置实例.

    参数:
        database_url: 可选的数据库 URL，覆盖 alembic.ini 中的 ``sqlalchemy.url``。

    返回:
        从 ``alembic.ini`` 加载的 ``Config`` 实例。
    """

    from alembic.config import Config

    config = Config(str(ALEMBIC_INI_PATH))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url)
    return config


def get_head_revision() -> str:
    """查询当前应用的 Alembic head revision.

    从 ``alembic.ini`` 加载脚本目录，返回唯一的 head revision ID。
    若存在多个 head 则抛出 ``RuntimeError``（SPEC 8.2: 禁止多 head）。

    返回:
        head revision 字符串。无 revision 时返回空字符串。
    """

    config = get_alembic_config()
    script_dir: ScriptDirectory = _get_script_directory(config)
    heads = script_dir.get_heads()
    if len(heads) > 1:
        raise RuntimeError(
            f"Alembic 存在多个 head: {heads}，SPEC 8.2 要求全局唯一 head",
        )
    return heads[0] if heads else ""


def _get_script_directory(config: Config) -> ScriptDirectory:
    """从配置创建 ScriptDirectory，应用模块注册表的 version_locations。"""

    from alembic.script import ScriptDirectory

    from app.composition.modules import MODULE_VERSION_LOCATIONS

    # SPEC 8.2: env.py 仅从模块注册表收集 version_locations。
    # 此处在创建 ScriptDirectory 前将注册表条目写入 config，
    # 使 ScriptDirectory 的版本扫描覆盖所有已启用模块。
    if MODULE_VERSION_LOCATIONS:
        existing = config.get_main_option("version_locations") or ""
        combined = (
            f"{existing} {' '.join(MODULE_VERSION_LOCATIONS)}".strip()
            if existing
            else " ".join(MODULE_VERSION_LOCATIONS)
        )
        config.set_main_option("version_locations", combined)

    return ScriptDirectory.from_config(config)
