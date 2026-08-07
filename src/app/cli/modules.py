"""modules validate 命令（SPEC §25.1）。

验证模块编码、路由、权限、错误码、事件和 Alembic 单头。
通过 :class:`ModuleRegistry` 执行全部注册校验（重复检测、依赖校验、循环检测），
并检查迁移脚本目录恰好有一个 head revision。
"""

from __future__ import annotations

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.composition_root import get_enabled_modules
from app.infrastructure.database.revision_check import SCRIPT_LOCATION
from app.modules.registry import ModuleRegistry


def modules_validate() -> int:
    """验证模块注册和 Alembic 单头（SPEC §25.1）。

    执行以下校验：
    1. 通过 ModuleRegistry 校验模块编码、路由、权限、错误码、事件等全局唯一性
    2. 检查 Alembic 迁移脚本目录恰好有一个 head revision

    任一校验失败时抛出异常，由 CLI 入口捕获并返回非 0 退出码。

    Returns:
        退出码：全部校验通过返回 0
    """
    # 模块注册校验（重复检测和依赖校验由 ModuleRegistry 在构造时完成）
    modules = get_enabled_modules()
    ModuleRegistry(modules)

    # Alembic 单头校验
    _assert_single_head()

    print(f"模块校验通过：{len(modules)} 个模块，0 个冲突，Alembic 单头正常")
    return 0


def _assert_single_head() -> None:
    """检查 Alembic 迁移脚本目录恰好有一个 head revision（SPEC §5.5、§8.2）。

    Raises:
        RuntimeError: 迁移图存在多个 head
    """
    alembic_config = AlembicConfig()
    alembic_config.set_main_option("script_location", SCRIPT_LOCATION)
    script_dir = ScriptDirectory.from_config(alembic_config)
    heads = script_dir.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Alembic 迁移图有 {len(heads)} 个 head，期望恰好一个：{heads}")
