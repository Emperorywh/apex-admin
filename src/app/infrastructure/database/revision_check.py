"""Alembic revision 一致性校验探针（SPEC §6.2、§26.1）。

实现 :class:`~app.health.providers.ReadinessProbe` 端口，为 ``/health/ready``
提供数据库当前 Alembic revision 与应用期望 revision 的一致性校验。

校验逻辑：
1. 从迁移脚本目录读取全局 head revision（应用期望值）
2. 从数据库 ``alembic_version`` 表读取当前 revision（数据库实际值）
3. 比较两者——不一致、多 head、表不存在或连接失败时返回 ``False``

SPEC §6.2：``GET /health/ready`` 验证当前 Alembic revision 与应用要求一致。
SPEC §26.1：未执行迁移时新版本就绪检查必须失败。
探针在每次 ``/health/ready`` 请求时执行，恢复后无需重启即可重新就绪。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.health.providers import ReadinessProbe

if TYPE_CHECKING:
    from app.infrastructure.database.db_pool_provider import SqlAlchemyDbPoolProvider

_logger = logging.getLogger("app.infrastructure.database.revision_check")

# 迁移脚本目录（与 alembic.ini 的 script_location 一致）
SCRIPT_LOCATION: str = str(Path(__file__).resolve().parent / "migrations")


class AlembicRevisionProbe(ReadinessProbe):
    """Alembic revision 一致性校验探针（SPEC §6.2、§26.1）。

    通过共享 :class:`~app.infrastructure.database.db_pool_provider.SqlAlchemyDbPoolProvider`
    的异步引擎查询数据库 ``alembic_version`` 表，并将结果与迁移脚本目录中的
    全局 head revision 比对。

    Args:
        db_pool_provider: 数据库连接池 provider，提供异步引擎
        script_location: Alembic 迁移脚本目录路径，默认为本包下的 ``migrations/``
    """

    def __init__(
        self,
        db_pool_provider: SqlAlchemyDbPoolProvider,
        *,
        script_location: str = SCRIPT_LOCATION,
    ) -> None:
        self._db_pool_provider = db_pool_provider
        self._script_location = script_location

    async def probe(self) -> bool:
        """执行 Alembic revision 一致性校验。

        任一前置条件不满足（引擎未初始化、多 head、表不存在、连接失败）
        或 revision 不一致时返回 ``False``，使 ``/health/ready`` 返回 503。
        """
        engine = self._db_pool_provider.engine
        if engine is None:
            _logger.warning("数据库引擎未初始化，Alembic revision 校验失败")
            return False

        try:
            expected_head = self._get_expected_head()
            if expected_head is None:
                _logger.warning("迁移脚本目录存在多个 head，revision 校验失败")
                return False

            current_revision = await self._get_current_revision(engine)
        except Exception:
            # 数据库不可用、alembic_version 表不存在或其他查询异常
            # 均视为 revision 校验失败（SPEC §26.1：未执行迁移时检查必须失败）
            _logger.warning("Alembic revision 校验失败", exc_info=True)
            return False

        if current_revision != expected_head:
            _logger.warning(
                "Alembic revision 不一致：期望 %s，实际 %s",
                expected_head,
                current_revision,
            )
            return False

        return True

    def _get_expected_head(self) -> str | None:
        """从迁移脚本目录读取全局 head revision。

        Returns:
            唯一 head 的 revision ID；存在多个 head 时返回 ``None``
        """
        alembic_config = AlembicConfig()
        alembic_config.set_main_option("script_location", self._script_location)
        script_dir = ScriptDirectory.from_config(alembic_config)
        heads = script_dir.get_heads()
        if len(heads) != 1:
            return None
        return heads[0]

    async def _get_current_revision(self, engine: AsyncEngine) -> str | None:
        """从数据库 ``alembic_version`` 表读取当前 revision。

        数据库从未执行迁移时 ``alembic_version`` 表不存在，查询抛出异常，
        由 :meth:`probe` 的异常处理统一捕获并返回 ``False``。

        Returns:
            当前 revision ID；表为空时返回 ``None``
        """
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            return str(row[0]) if row is not None else None
