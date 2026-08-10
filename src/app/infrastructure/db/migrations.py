"""Alembic 迁移工具函数 — SPEC 8.2.

提供 Alembic 配置加载、head revision 查询和迁移执行功能。
供 CLI ``db upgrade`` 命令、``modules validate`` 命令和健康检查器使用。

SPEC 5.2 分层约束: Infrastructure 层不得反向导入 Composition Root。
``version_locations`` 通过参数传入，由调用方（CLI 或 lifespan）从
Composition Root 的模块清单中获取后传递。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alembic.config import Config
    from alembic.script import ScriptDirectory

# 项目根目录 — 本文件位于 src/app/infrastructure/db/migrations.py，
# 向上 4 级到达项目根（包含 pyproject.toml 和 alembic.ini 的目录）。
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[4]

#: alembic.ini 文件路径
ALEMBIC_INI_PATH: Path = _PROJECT_ROOT / "alembic.ini"


def get_alembic_config(
    database_url: str | None = None,
    version_locations: Sequence[str] | None = None,
) -> Config:
    """构造 Alembic 配置实例.

    参数:
        database_url: 可选的数据库 URL，覆盖 alembic.ini 中的 ``sqlalchemy.url``。
        version_locations: 模块迁移版本目录列表，由调用方从 Composition Root
                           的模块清单中获取后传入。

    返回:
        从 ``alembic.ini`` 加载的 ``Config`` 实例。
    """

    from alembic.config import Config

    config = Config(str(ALEMBIC_INI_PATH))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url)
    if version_locations:
        _apply_version_locations(config, version_locations)
    return config


def get_head_revision(
    version_locations: Sequence[str] | None = None,
) -> str:
    """查询当前应用的 Alembic head revision.

    从 ``alembic.ini`` 加载脚本目录，返回唯一的 head revision ID。
    若存在多个 head 则抛出 ``RuntimeError``（SPEC 8.2: 禁止多 head）。

    参数:
        version_locations: 模块迁移版本目录列表，由调用方从 Composition Root
                           的模块清单中获取后传入。

    返回:
        head revision 字符串。无 revision 时返回空字符串。
    """

    config = get_alembic_config(version_locations=version_locations)
    script_dir: ScriptDirectory = _get_script_directory(config)
    heads = script_dir.get_heads()
    if len(heads) > 1:
        raise RuntimeError(
            f"Alembic 存在多个 head: {heads}，SPEC 8.2 要求全局唯一 head",
        )
    return heads[0] if heads else ""


def _apply_version_locations(
    config: Config,
    version_locations: Sequence[str],
) -> None:
    """将模块版本目录应用到 Alembic 配置.

    SPEC 8.2: env.py 仅从模块注册表收集 version_locations。
    此处将注册表条目写入 config，使 ScriptDirectory 的版本扫描
    覆盖所有已启用模块。

    Alembic 的 ``version_locations`` 配置项覆盖默认的 ``versions`` 目录
    而非追加。因此必须显式包含默认 ``versions`` 目录，确保框架初始迁移
    （``alembic/versions/``）和各模块迁移版本目录共同组成全局单头
    revision 图（SPEC 5.5 / 8.2）。
    """

    existing = config.get_main_option("version_locations") or ""
    # 始终包含默认 versions 目录。
    # Alembic 在此版本中将 version_locations 相对于 CWD 解析，
    # 因此使用 ``<script_location>/versions`` 显式指定路径。
    script_loc = config.get_main_option("script_location") or "alembic"
    default_versions = f"{script_loc}/versions"
    all_locations = [default_versions, *version_locations]
    combined = (
        f"{existing} {' '.join(all_locations)}".strip()
        if existing
        else " ".join(all_locations)
    )
    config.set_main_option("version_locations", combined)


def _get_script_directory(config: Config) -> ScriptDirectory:
    """从配置创建 ScriptDirectory。"""

    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(config)
