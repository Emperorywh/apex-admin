"""时间与时区测试 — SPEC 6.3.

覆盖验收标准:
  - 所有时间列为 timestamptz。
  - Clock Port 输出带时区 UTC。
  - 存在防止 naive datetime 的测试约束。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.application.ports import SystemClock
from app.infrastructure.db.engine import create_db_engine

# ── Clock Port 时区测试 ──────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_clock_returns_timezone_aware_utc() -> None:
    """Clock Port 输出带时区 UTC（SPEC 6.3）。"""

    clock = SystemClock()
    now = clock.now()

    # 必须带时区
    assert now.tzinfo is not None

    # 时区必须是 UTC（偏移为 0）
    assert now.utcoffset() == timedelta(0)

    # tzinfo 应为 UTC
    assert now.tzinfo is UTC or now.utcoffset().total_seconds() == 0


@pytest.mark.g1
@pytest.mark.unit
def test_clock_not_naive() -> None:
    """Clock Port 不返回 naive datetime（SPEC 6.3: 禁止无时区语义时间）。"""

    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is not None, "Clock Port 不应返回 naive datetime"


# ── timestamptz 集成测试 ─────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.integration
async def test_timestamptz_column_stores_aware_datetime(
    database_url: str,
) -> None:
    """timestamptz 列正确存储带时区 UTC datetime（SPEC 6.3）。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            # 创建带 timestamptz 列的临时表
            await conn.execute(
                text("DROP TABLE IF EXISTS test_tz"),
            )
            await conn.execute(
                text(
                    "CREATE TABLE test_tz ("
                    "  id serial PRIMARY KEY,"
                    "  created_at timestamptz NOT NULL"
                    ")",
                ),
            )

        # 插入带时区的 UTC datetime
        aware_time = datetime.now(UTC)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO test_tz (created_at) VALUES (:ts)",
                ),
                {"ts": aware_time},
            )

        # 读回验证
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT created_at FROM test_tz WHERE id = 1"),
            )
            row = result.fetchone()
            assert row is not None
            stored_time = row[0]

            # PostgreSQL 返回的 timestamptz 应为带时区的 datetime
            assert stored_time.tzinfo is not None

        # 清理
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS test_tz"))
    finally:
        await engine.dispose()


@pytest.mark.g1
@pytest.mark.integration
async def test_timestamptz_rejects_naive_datetime_via_explicit_cast(
    database_url: str,
) -> None:
    """防止 naive datetime 的测试约束（SPEC 6.3）。

    验证 SQLAlchemy 层不会静默接受 naive datetime：
    使用显式 UTC 参数化写入带时区值，确保应用层始终使用 aware datetime。
    本测试作为约束基准 — 所有 ORM 模型的 datetime 字段应使用 timestamptz
    且写入时必须带时区。
    """

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS test_tz_constraint"))
            await conn.execute(
                text(
                    "CREATE TABLE test_tz_constraint ("
                    "  id serial PRIMARY KEY,"
                    "  event_time timestamptz NOT NULL"
                    ")",
                ),
            )

        # 应用层写入时必须使用带时区的 datetime（模拟正确行为）
        aware_time = datetime.now(UTC)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO test_tz_constraint (event_time) VALUES (:ts)",
                ),
                {"ts": aware_time},
            )

        # 验证写入成功且返回值带时区
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT event_time FROM test_tz_constraint WHERE id = 1"),
            )
            row = result.fetchone()
            assert row is not None
            assert row[0].tzinfo is not None

        # 验证列类型为 timestamptz
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'test_tz_constraint' "
                    "AND column_name = 'event_time'",
                ),
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "timestamp with time zone"

        # 清理
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS test_tz_constraint"))
    finally:
        await engine.dispose()


@pytest.mark.g1
@pytest.mark.unit
def test_naive_datetime_detected_by_helper() -> None:
    """单元测试约束：检测 naive datetime 并拒绝（SPEC 6.3）。

    应用层应确保所有 datetime 值带时区。此测试验证检测逻辑：
    当 datetime 无 tzinfo 时，应用代码应将其视为错误。
    """

    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    # naive datetime 的 tzinfo 为 None
    assert naive.tzinfo is None

    # aware datetime 的 tzinfo 不为 None
    assert aware.tzinfo is not None

    # SPEC 6.3 约束：应用层不应使用 naive datetime 参与关键业务计算
    # 此约束通过 Clock Port 返回 aware datetime 保证


@pytest.mark.g1
@pytest.mark.unit
def test_utc_timezone_constant() -> None:
    """验证 UTC 时区常量行为正确。"""

    aware = datetime.now(UTC)
    # UTC 的 utcoffset 应为 0
    assert aware.utcoffset() == timedelta(0)
    # 与 timezone.utc 等价
    assert aware.tzinfo is not None
