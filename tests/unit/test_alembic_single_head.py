"""Alembic 单头 revision 图检查（SPEC §5.5、§8.2、§28.2）。

CI 兼容的单头校验：检测多 head 时失败。
此测试不需要数据库连接，仅读取迁移脚本目录中的 revision 图结构。

SPEC §5.5：每个新 revision 的 ``down_revision`` 必须指向生成时的全局 head；
CI 检测到多 head 时失败。
SPEC §28.2：测试 Alembic 只有一个 head。
"""

from __future__ import annotations

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.infrastructure.database.revision_check import SCRIPT_LOCATION

pytestmark = [pytest.mark.unit, pytest.mark.g1]


class TestSingleHead:
    """验证 Alembic revision 图恰好有一个 head。"""

    def test_exactly_one_head(self) -> None:
        """迁移脚本目录恰好包含一个 head revision。

        多 head 会导致 CI 失败（SPEC §5.5、§8.2）。
        """
        config = AlembicConfig()
        config.set_main_option("script_location", SCRIPT_LOCATION)
        script_dir = ScriptDirectory.from_config(config)
        heads = script_dir.get_heads()

        assert len(heads) == 1, f"期望恰好一个 head，实际有 {len(heads)} 个：{heads}"

    def test_head_matches_latest_revision(self) -> None:
        """当前 head 为最新 revision（权限点注册表迁移加入后为 0008）。"""
        config = AlembicConfig()
        config.set_main_option("script_location", SCRIPT_LOCATION)
        script_dir = ScriptDirectory.from_config(config)
        heads = script_dir.get_heads()

        assert heads == ["0008"]

    def test_no_branch_labels(self) -> None:
        """G1 基线不使用分支标签，确保全局 revision 图线性。"""
        config = AlembicConfig()
        config.set_main_option("script_location", SCRIPT_LOCATION)
        script_dir = ScriptDirectory.from_config(config)

        # 遍历全部 revision，确认无分支标签
        for revision in script_dir.walk_revisions():
            assert not revision.branch_labels, f"revision {revision.revision} 不应携带分支标签"
