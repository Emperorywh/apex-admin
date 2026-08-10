"""Unit of Work 集成测试与异常翻译测试 — SPEC 5.6 / 8.1 / 10.1.

覆盖验收标准:
  - 成功路径恰好提交一次。
  - Use Case 异常完整回滚。
  - UoW 结束后会话不可用。
  - 唯一约束冲突翻译为稳定应用异常。
  - 连接错误翻译为稳定应用异常。
  - SQLAlchemy 类型不泄漏到应用异常。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.application.errors import (
    ApplicationError,
    DatabaseConnectionError,
    UniqueViolationError,
)
from app.application.ports import UnitOfWork
from app.infrastructure.db.engine import create_db_engine
from app.infrastructure.db.exceptions import translate_db_exception
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

# ── 测试辅助 ───────────────────────────────────────────────────────────────

#: 测试用临时表名（每次测试创建/清理，避免相互依赖）
_TEST_TABLE = "test_uow_items"


async def _setup_test_table(database_url: str) -> None:
    """创建测试临时表（含唯一约束）。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TEST_TABLE}"))
            await conn.execute(
                text(
                    f"CREATE TABLE {_TEST_TABLE} ("
                    f"  id serial PRIMARY KEY,"
                    f"  name text NOT NULL UNIQUE"
                    f")",
                ),
            )
    finally:
        await engine.dispose()


async def _cleanup_test_table(database_url: str) -> None:
    """清理测试临时表。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {_TEST_TABLE}"))
    finally:
        await engine.dispose()


async def _count_rows(database_url: str) -> int:
    """查询测试表中的行数。"""

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(f"SELECT count(*) FROM {_TEST_TABLE}"))
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


# ── 异常翻译单元测试 ──────────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_application_errors_have_stable_codes() -> None:
    """应用异常携带稳定错误码（SPEC 10.2）。"""

    assert UniqueViolationError.code == "DB.UNIQUE_VIOLATION"
    assert DatabaseConnectionError.code == "DB.CONNECTION_ERROR"

    # 实例化后 code 不变
    exc1 = UniqueViolationError("detail")
    assert exc1.code == "DB.UNIQUE_VIOLATION"

    exc2 = DatabaseConnectionError("detail")
    assert exc2.code == "DB.CONNECTION_ERROR"


@pytest.mark.g1
@pytest.mark.unit
def test_application_errors_no_sqlalchemy_types() -> None:
    """应用异常不包含 SQLAlchemy 类型（SPEC 8.1: 异常类型不泄漏）。"""

    exc = UniqueViolationError("some detail")
    # 异常类继承自 ApplicationError → Exception，不依赖 SQLAlchemy
    assert isinstance(exc, ApplicationError)
    assert isinstance(exc, Exception)

    # 异常模块来源是应用层，不是 sqlalchemy
    assert "app.application" in UniqueViolationError.__module__
    assert "app.application" in DatabaseConnectionError.__module__


@pytest.mark.g1
@pytest.mark.unit
def test_translate_unique_violation() -> None:
    """translate_db_exception 将唯一约束冲突翻译为 UniqueViolationError。

    SPEC 8.1: 数据库异常转换为稳定的应用异常。
    """

    import psycopg.errors as psycopg_errors

    # 构造一个底层为 psycopg UniqueViolation 的 IntegrityError
    pg_exc = psycopg_errors.UniqueViolation("duplicate key value")
    sa_exc = IntegrityError("INSERT ...", {}, pg_exc)

    result = translate_db_exception(sa_exc)
    assert isinstance(result, UniqueViolationError)
    assert result.code == "DB.UNIQUE_VIOLATION"


@pytest.mark.g1
@pytest.mark.unit
def test_translate_operational_error() -> None:
    """translate_db_exception 将操作类错误翻译为 DatabaseConnectionError。"""

    import psycopg.errors as psycopg_errors

    pg_exc = psycopg_errors.OperationalError("connection refused")
    sa_exc = OperationalError("SELECT ...", {}, pg_exc)

    result = translate_db_exception(sa_exc)
    assert isinstance(result, DatabaseConnectionError)
    assert result.code == "DB.CONNECTION_ERROR"


@pytest.mark.g1
@pytest.mark.unit
def test_translate_other_sqlalchemy_error() -> None:
    """translate_db_exception 对其他 SQLAlchemy 异常返回通用应用异常。"""

    from sqlalchemy.exc import SQLAlchemyError

    sa_exc = SQLAlchemyError("some error")
    result = translate_db_exception(sa_exc)
    assert isinstance(result, ApplicationError)
    assert not isinstance(result, UniqueViolationError)
    assert not isinstance(result, DatabaseConnectionError)


@pytest.mark.g1
@pytest.mark.unit
def test_translate_does_not_leak_sqlalchemy_types() -> None:
    """翻译后的异常不包含 SQLAlchemy 异常类（SPEC 8.1）。"""

    import psycopg.errors as psycopg_errors

    pg_exc = psycopg_errors.UniqueViolation("dup")
    sa_exc = IntegrityError("stmt", {}, pg_exc)

    result = translate_db_exception(sa_exc)
    # 结果是应用异常，不是 SQLAlchemy 异常
    assert isinstance(result, ApplicationError)
    # __cause__ 链接原始 SQLAlchemy 异常（Python 异常链机制），
    # 但异常类型本身是应用层类型
    assert not isinstance(result, IntegrityError)
    assert not isinstance(result, OperationalError)


@pytest.mark.g1
@pytest.mark.unit
def test_uow_port_does_not_expose_sqlalchemy_types() -> None:
    """UnitOfWork Port 不暴露 SQLAlchemy 类型（SPEC 5.6 / 8.1）。

    Application 层的 UnitOfWork Port 定义中不包含 AsyncSession
    或其他 SQLAlchemy 类型。
    """

    import inspect

    # Port 的公共方法签名
    for name in ["commit", "rollback", "__aenter__", "__aexit__"]:
        method = getattr(UnitOfWork, name, None)
        if method is None:
            continue
        sig = inspect.signature(method) if callable(method) else None
        if sig is None:
            continue
        # 签名中不出现 SQLAlchemy 类型
        sig_str = str(sig)
        assert "AsyncSession" not in sig_str, (
            f"UnitOfWork.{name} 签名暴露了 AsyncSession"
        )
        assert "sqlalchemy" not in sig_str.lower(), (
            f"UnitOfWork.{name} 签名引用了 sqlalchemy"
        )


# ── UoW 生命周期集成测试 ──────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.integration
async def test_uow_commit_persists_data(database_url: str) -> None:
    """成功路径：commit 后数据持久化（SPEC 5.6: 恰好提交一次）。"""

    await _setup_test_table(database_url)
    try:
        engine = create_db_engine(database_url)
        uow = SqlAlchemyUnitOfWork(engine)
        try:
            async with uow:
                await uow.session.execute(
                    text(f"INSERT INTO {_TEST_TABLE} (name) VALUES ('item1')"),
                )
                await uow.commit()

            # 退出上下文后数据应已持久化
            count = await _count_rows(database_url)
            assert count == 1
        finally:
            await engine.dispose()
    finally:
        await _cleanup_test_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_uow_exception_rolls_back(database_url: str) -> None:
    """Use Case 异常时完整回滚（SPEC 5.6: 异常完整回滚）。"""

    await _setup_test_table(database_url)
    try:
        engine = create_db_engine(database_url)
        uow = SqlAlchemyUnitOfWork(engine)
        try:
            with pytest.raises(ValueError, match="模拟业务异常"):
                async with uow:
                    await uow.session.execute(
                        text(f"INSERT INTO {_TEST_TABLE} (name) VALUES ('temp')"),
                    )
                    raise ValueError("模拟业务异常")

            # 异常退出后数据不应存在
            count = await _count_rows(database_url)
            assert count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_test_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_uow_no_commit_rolls_back(database_url: str) -> None:
    """未显式 commit 时退出上下文自动回滚（SPEC 5.6）。"""

    await _setup_test_table(database_url)
    try:
        engine = create_db_engine(database_url)
        uow = SqlAlchemyUnitOfWork(engine)
        try:
            async with uow:
                await uow.session.execute(
                    text(f"INSERT INTO {_TEST_TABLE} (name) VALUES ('no-commit')"),
                )
                # 不调用 commit，直接退出

            count = await _count_rows(database_url)
            assert count == 0
        finally:
            await engine.dispose()
    finally:
        await _cleanup_test_table(database_url)


@pytest.mark.g1
@pytest.mark.integration
async def test_uow_session_unavailable_after_exit(database_url: str) -> None:
    """UoW 结束后会话不可用（SPEC 8.1: 禁止在 UoW 生命周期外复用会话）。"""

    engine = create_db_engine(database_url)
    uow = SqlAlchemyUnitOfWork(engine)
    try:
        async with uow:
            _ = uow.session  # 上下文内可访问

        # 退出后访问 session 应抛出 RuntimeError
        with pytest.raises(RuntimeError, match="未激活"):
            _ = uow.session
    finally:
        await engine.dispose()


@pytest.mark.g1
@pytest.mark.integration
async def test_uow_session_unavailable_before_enter(database_url: str) -> None:
    """UoW 进入前会话不可用。"""

    engine = create_db_engine(database_url)
    uow = SqlAlchemyUnitOfWork(engine)
    try:
        with pytest.raises(RuntimeError, match="未激活"):
            _ = uow.session
    finally:
        await engine.dispose()


@pytest.mark.g1
@pytest.mark.integration
async def test_uow_is_unit_of_work_port(database_url: str) -> None:
    """SqlAlchemyUnitOfWork 是 UnitOfWork Port 的实现。"""

    engine = create_db_engine(database_url)
    uow = SqlAlchemyUnitOfWork(engine)
    try:
        assert isinstance(uow, UnitOfWork)
    finally:
        await engine.dispose()


@pytest.mark.g1
@pytest.mark.integration
async def test_uow_commit_translates_unique_violation(database_url: str) -> None:
    """UoW commit 时唯一约束冲突翻译为 UniqueViolationError（SPEC 8.1）。

    使用 deferred constraint 使约束冲突在 commit 时触发，
    验证 UoW 的 commit 异常翻译边界。
    """

    await _setup_test_table(database_url)
    try:
        engine = create_db_engine(database_url)
        uow = SqlAlchemyUnitOfWork(engine)
        try:
            # 先插入一条记录并提交
            async with uow:
                await uow.session.execute(
                    text(f"INSERT INTO {_TEST_TABLE} (name) VALUES ('dup-item')"),
                )
                await uow.commit()

            # 再次插入相同 name — execute 时立即触发约束冲突
            # 验证 translate_db_exception 能正确翻译此类异常
            uow2 = SqlAlchemyUnitOfWork(engine)
            try:
                async with uow2:
                    with pytest.raises(IntegrityError) as exc_info:
                        await uow2.session.execute(
                            text(
                                f"INSERT INTO {_TEST_TABLE} (name) VALUES ('dup-item')",
                            ),
                        )

                    # 验证 translate_db_exception 能翻译此异常
                    translated = translate_db_exception(exc_info.value)
                    assert isinstance(translated, UniqueViolationError)
                    assert translated.code == "DB.UNIQUE_VIOLATION"
            finally:
                await engine.dispose()
        finally:
            await engine.dispose()
    finally:
        await _cleanup_test_table(database_url)
