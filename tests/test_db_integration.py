"""数据库连通性冒烟测试.

在真实 PostgreSQL 上执行 ``SELECT 1`` 并断言 ``server_version`` 主版本为 18，
验证测试数据库供应链（SPEC 28.2）可正确提供可用的 PostgreSQL 18 实例。
"""

import psycopg
import pytest


@pytest.mark.g1
@pytest.mark.integration
def test_postgres_connectivity_and_version(database_url: str) -> None:
    """冒烟测试：真实 PostgreSQL 连通性与主版本号校验。

    1. 执行 ``SELECT 1`` 验证数据库可正常响应查询。
    2. 查询 ``SHOW server_version`` 并断言主版本为 18。
    """

    # psycopg 连接串使用 postgresql://（不带 +psycopg 后缀）。
    conninfo = database_url.replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )

    with (
        psycopg.connect(conninfo) as conn,
        conn.cursor() as cur,
    ):
        # 基础连通性验证。
        cur.execute("SELECT 1")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 1

        # 主版本校验 — SPEC 5.4 固定 PostgreSQL 18.x。
        cur.execute("SHOW server_version")
        version_str = cur.fetchone()[0]
        major = int(version_str.split(".")[0])
        assert major == 18, f"期望主版本 18，实际 {version_str}"
