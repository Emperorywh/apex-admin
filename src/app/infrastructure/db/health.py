"""数据库健康检查器 — SPEC 6.2.

实现 ``HealthCheck`` Port，在 Infrastructure 层执行数据库连通性
与迁移版本检查。API 层通过 Port 调用，不直接依赖 SQLAlchemy 或 Alembic。

SPEC 6.2:
  - ``GET /health/ready`` 验证数据库连接和当前 Alembic revision
    与应用要求一致。
  - 就绪条件任一失败时返回稳定错误码。
  - 恢复后无需重启进程即可重新就绪（由 ``pool_pre_ping`` 保证）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.application.ports import HealthCheck, HealthResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

# 健康检查稳定错误码（SPEC 6.2 / 10.2）
_HEALTHY_CODE = "DB.OK"
_UNAVAILABLE_CODE = "DB.UNAVAILABLE"
_REVISION_MISMATCH_CODE = "DB.REVISION_MISMATCH"
_NO_REVISION_CODE = "DB.NO_REVISION"


class DbHealthChecker(HealthCheck):
    """数据库健康检查器 — SPEC 6.2 实现.

    检查内容:
      1. 数据库连通性（``SELECT 1``）。
      2. ``alembic_version`` 表存在且版本号与应用 head 一致。

    使用共享 ``AsyncEngine`` 的连接池，``pool_pre_ping`` 确保恢复后
    无需重启即可重新获取连接。
    """

    def __init__(self, engine: AsyncEngine, expected_revision: str) -> None:
        """初始化健康检查器.

        参数:
            engine:            共享异步引擎实例。
            expected_revision: 应用要求的 Alembic head revision。
        """

        self._engine: AsyncEngine = engine
        self._expected_revision: str = expected_revision

    async def check_ready(self) -> HealthResult:
        """执行就绪检查.

        返回:
            - 数据库可用且 revision 一致: ``healthy=True``
            - 数据库不可用: ``healthy=False``, ``code=DB.UNAVAILABLE``
            - revision 不匹配: ``healthy=False``, ``code=DB.REVISION_MISMATCH``
            - 无 revision 记录: ``healthy=False``, ``code=DB.NO_REVISION``
        """

        from sqlalchemy import text

        try:
            async with self._engine.connect() as conn:
                # 1. 连通性验证
                await conn.execute(text("SELECT 1"))

                # 2. 迁移版本一致性验证
                result = await conn.execute(
                    text("SELECT version_num FROM alembic_version"),
                )
                row = result.fetchone()
                if row is None:
                    return HealthResult(
                        healthy=False,
                        code=_NO_REVISION_CODE,
                        detail="数据库未执行迁移（alembic_version 表为空）",
                    )

                db_revision: str = str(row[0])
                if db_revision != self._expected_revision:
                    return HealthResult(
                        healthy=False,
                        code=_REVISION_MISMATCH_CODE,
                        detail=(
                            f"数据库 revision '{db_revision}' "
                            f"与应用 head '{self._expected_revision}' 不一致"
                        ),
                    )

                return HealthResult(
                    healthy=True,
                    code=_HEALTHY_CODE,
                    detail="数据库可用且迁移版本一致",
                )
        except Exception:
            # 数据库不可用（连接失败、表不存在等）
            return HealthResult(
                healthy=False,
                code=_UNAVAILABLE_CODE,
                detail="无法连接数据库或查询迁移版本",
            )
