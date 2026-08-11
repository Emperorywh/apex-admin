"""备份创建与验证服务 — SPEC 27.1 / 27.2 / 27.3.

SPEC 27.1:
  - 默认每天执行一次 PostgreSQL 逻辑全量备份（pg_dump）。
  - 备份失败能够被发现（非 0 退出码 + 结构化日志）。
  - 每月至少自动恢复一次最新备份到隔离环境。

SPEC 27.2:
  - 只将 READY 文件及备份清单纳入文件备份。
  - 文件物理删除至少延迟 7 天，确保备份窗口内清单引用的文件仍可复制。

SPEC 27.3:
  - 恢复后将最新备份恢复到隔离数据库并通过迁移版本、数据完整性、
    文件一致性检查。
  - 输出含 Backup ID/起止时间/实际 RPO/实际 RTO/检查结果的结构化演练报告。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import structlog

from app.modules.backup.errors import (
    BackupCreationError,
    BackupVerificationError,
)
from app.modules.backup.manifest import (
    build_file_entries,
    compute_manifest_hash,
    read_manifest,
    serialize_backup_set,
)
from app.modules.backup.models import (
    BackupReport,
    BackupSet,
    CheckResult,
    FileManifestEntry,
)
from app.modules.backup.retention import (
    apply_retention,
)

logger = structlog.get_logger(__name__)

#: RPO 目标（小时）— SPEC 27.1: "默认 RPO 不超过 24 小时"
RPO_TARGET_HOURS: float = 24.0

#: RTO 目标（小时）— SPEC 27.1: "默认恢复目标 RTO 不超过 4 小时"
RTO_TARGET_HOURS: float = 4.0


# ── pg_dump / psql 二进制解析 ─────────────────────────────────────────────


def find_pg_bin_dir() -> Path | None:
    """查找 PostgreSQL 客户端工具目录.

    优先使用 PATH 中的 pg_dump（生产环境/Docker），
    回退到 dev_db 供应的本地二进制目录（开发/测试环境）。
    """

    # 1. PATH 中的 pg_dump
    pg_dump_path = shutil.which("pg_dump")
    if pg_dump_path:
        return Path(pg_dump_path).parent

    # 2. dev_db 供应的本地二进制
    scripts_dir = Path(__file__).resolve().parents[4] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import dev_db  # type: ignore[import-not-found]

        if dev_db.is_provisioned():
            bin_dir: Path = dev_db.get_pg_bin_dir()
            return bin_dir
    except ImportError:
        pass

    return None


def _get_pg_executable(bin_dir: Path | None, name: str) -> str:
    """获取 PostgreSQL 可执行文件路径."""

    if bin_dir is None:
        # 依赖 PATH
        exe = shutil.which(name)
        if exe is None:
            msg = f"找不到 PostgreSQL 工具: {name}（不在 PATH 且无本地供应）"
            raise BackupCreationError(msg)
        return exe

    suffix = ".exe" if sys.platform == "win32" else ""
    return str(bin_dir / f"{name}{suffix}")


def _parse_db_url(database_url: str) -> dict[str, str | int | None]:
    """解析 SQLAlchemy 数据库 URL 为连接参数字典.

    将 ``postgresql+psycopg://user:pass@host:port/db`` 拆解为
    host/port/user/password/dbname 组件，供 pg_dump / psql 使用。
    """

    # 替换驱动前缀为标准 postgresql://
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": parsed.username or "apex",
        "password": parsed.password,
        "dbname": (parsed.path or "/").lstrip("/"),
    }


# ── 备份创建 ───────────────────────────────────────────────────────────────


async def create_backup(
    *,
    database_url: str,
    output_dir: Path,
    storage_root: str,
    daily_retention: int,
    weekly_retention: int,
) -> BackupSet:
    """创建一个完整备份集 — SPEC 27.1 / 27.2.

    流程:
      1. 检查数据库连通性（失败时抛出 BackupCreationError，SPEC 27.1）。
      2. 生成唯一 Backup ID 并创建备份集目录。
      3. 执行 pg_dump 逻辑全量备份。
      4. 查询 READY 文件元数据，构建文件清单。
      5. 复制 READY 物理文件到备份集目录。
      6. 计算清单哈希并写入 manifest.json。
      7. 执行滚动保留清理。

    参数:
        database_url: 源数据库 SQLAlchemy URL。
        output_dir:   备份输出目录（包含多个备份集）。
        storage_root: 文件存储根目录（用于查找 READY 物理文件）。
        daily_retention: 日备份保留数量。
        weekly_retention: 周备份保留数量。

    返回:
        创建的 BackupSet 实例。

    异常:
        BackupCreationError: 数据库不可用或备份流程失败。
    """

    bin_dir = find_pg_bin_dir()
    params = _parse_db_url(database_url)

    # ── 1. 数据库连通性检查 ──
    if not await _check_db_available(database_url):
        logger.error(
            "backup.create.db_unavailable",
            host=params["host"],
            port=params["port"],
            dbname=params["dbname"],
        )
        msg = "数据库不可用，无法创建备份"
        raise BackupCreationError(msg)

    # ── 2. 生成 Backup ID 与目录 ──
    now = datetime.now(UTC)
    backup_id = _generate_backup_id(now)
    backup_dir = output_dir / backup_id
    files_dir = backup_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    dump_file = "database.sql"
    dump_path = backup_dir / dump_file

    logger.info(
        "backup.create.started",
        backup_id=backup_id,
        output_dir=str(output_dir),
    )

    try:
        # ── 3. pg_dump 逻辑全量备份 ──
        _run_pg_dump(bin_dir, params, dump_path)
        logger.info(
            "backup.create.dump_done",
            backup_id=backup_id,
            dump_file=str(dump_path),
            dump_size=dump_path.stat().st_size,
        )

        # ── 4. 查询 READY 文件并构建清单 ──
        entries = await _build_manifest_from_db(database_url)

        # ── 5. 复制 READY 物理文件 ──
        for entry in entries:
            src = Path(storage_root) / "files" / entry.storage_name
            dst = files_dir / entry.storage_name
            if src.exists():
                shutil.copy2(str(src), str(dst))

        # ── 6. 计算清单哈希并写入 manifest ──
        manifest_hash = compute_manifest_hash(entries)
        total_size = sum(e.size_bytes for e in entries)

        backup_set = BackupSet(
            backup_id=backup_id,
            created_at=now,
            database_dump_file=dump_file,
            files=entries,
            file_count=len(entries),
            total_size_bytes=total_size,
            manifest_sha256=manifest_hash,
        )

        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                serialize_backup_set(backup_set),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        logger.info(
            "backup.create.completed",
            backup_id=backup_id,
            file_count=len(entries),
            total_size_bytes=total_size,
            manifest_sha256=manifest_hash,
        )

        # ── 7. 滚动保留清理 ──
        result = apply_retention(
            output_dir,
            daily_retention=daily_retention,
            weekly_retention=weekly_retention,
        )
        for d in result.delete_dirs:
            shutil.rmtree(str(d), ignore_errors=True)
            logger.info("backup.create.retention_deleted", backup_dir=d.name)

        logger.info(
            "backup.create.retention",
            kept=len(result.keep_dirs),
            deleted=len(result.delete_dirs),
        )

    except BackupCreationError:
        raise
    except Exception as exc:
        logger.error(
            "backup.create.failed",
            backup_id=backup_id,
            error=str(exc),
        )
        msg = f"备份创建失败: {exc}"
        raise BackupCreationError(msg) from exc

    return backup_set


# ── 备份验证 ───────────────────────────────────────────────────────────────


async def verify_backup(
    *,
    backup_dir: Path,
    source_database_url: str,
    report_path: Path | None = None,
) -> BackupReport:
    """验证备份集: 恢复到隔离库并执行检查 — SPEC 27.3.

    流程:
      1. 读取备份集 manifest.json。
      2. 创建隔离数据库，恢复 pg_dump 输出。
      3. 执行迁移版本检查（恢复库的 alembic head == 源库 head）。
      4. 执行数据完整性检查（循环检测、孤立关联）。
      5. 执行文件一致性检查（备份文件 SHA-256 与清单匹配）。
      6. 生成结构化演练报告。

    参数:
        backup_dir: 备份集目录（包含 manifest.json）。
        source_database_url: 源数据库 URL（用于读取迁移 head 版本）。
        report_path: 报告输出路径（None 时不写文件）。

    返回:
        BackupReport 演练报告。

    异常:
        BackupVerificationError: 恢复过程出错。
    """

    started_at = datetime.now(UTC)

    # 读取备份集
    backup_set = read_manifest(backup_dir)
    logger.info(
        "backup.verify.started",
        backup_id=backup_set.backup_id,
    )

    bin_dir = find_pg_bin_dir()
    params = _parse_db_url(source_database_url)

    # 创建隔离数据库
    iso_db_name = f"apex_verify_{uuid4().hex[:12]}"
    iso_params = {**params, "dbname": iso_db_name}

    failure_reason: str | None = None

    try:
        _create_isolated_database(bin_dir, params, iso_db_name)
        logger.info(
            "backup.verify.isolated_db_created",
            database=iso_db_name,
        )

        # 恢复数据库备份
        dump_path = backup_dir / backup_set.database_dump_file
        if not dump_path.exists():
            msg = f"数据库备份文件不存在: {dump_path}"
            raise BackupVerificationError(msg)

        _run_psql_restore(bin_dir, iso_params, dump_path)
        logger.info(
            "backup.verify.restore_done",
            database=iso_db_name,
        )

        iso_db_url = _build_db_url(iso_params)

        # ── 检查 1: 迁移版本 ──
        migration_result = await _check_migration_version(
            iso_db_url,
            source_database_url,
        )

        # ── 检查 2: 数据完整性 ──
        integrity_result = await _check_data_integrity(iso_db_url)

        # ── 检查 3: 文件一致性 ──
        file_result = _check_file_consistency(backup_dir, backup_set)

    except BackupVerificationError as exc:
        failure_reason = str(exc)
        migration_result = CheckResult(passed=False, detail=f"验证流程中断: {exc}")
        integrity_result = CheckResult(passed=False, detail="未执行")
        file_result = CheckResult(passed=False, detail="未执行")
    except Exception as exc:
        failure_reason = str(exc)
        logger.error(
            "backup.verify.failed",
            backup_id=backup_set.backup_id,
            error=str(exc),
        )
        migration_result = CheckResult(passed=False, detail=f"验证异常: {exc}")
        integrity_result = CheckResult(passed=False, detail="未执行")
        file_result = CheckResult(passed=False, detail="未执行")
    finally:
        # 清理隔离数据库
        with contextlib.suppress(Exception):
            _drop_isolated_database(bin_dir, params, iso_db_name)
            logger.info(
                "backup.verify.isolated_db_dropped",
                database=iso_db_name,
            )

    finished_at = datetime.now(UTC)
    actual_rpo_hours = (started_at - backup_set.created_at).total_seconds() / 3600.0
    actual_rto_hours = (finished_at - started_at).total_seconds() / 3600.0

    all_passed = (
        migration_result.passed
        and integrity_result.passed
        and file_result.passed
        and failure_reason is None
        and actual_rpo_hours <= RPO_TARGET_HOURS
        and actual_rto_hours <= RTO_TARGET_HOURS
    )

    if not all_passed and failure_reason is None:
        failures: list[str] = []
        if not migration_result.passed:
            failures.append("迁移版本检查未通过")
        if not integrity_result.passed:
            failures.append("数据完整性检查未通过")
        if not file_result.passed:
            failures.append("文件一致性检查未通过")
        if actual_rpo_hours > RPO_TARGET_HOURS:
            failures.append(f"RPO 超标: {actual_rpo_hours:.1f}h > {RPO_TARGET_HOURS}h")
        if actual_rto_hours > RTO_TARGET_HOURS:
            failures.append(f"RTO 超标: {actual_rto_hours:.1f}h > {RTO_TARGET_HOURS}h")
        failure_reason = "; ".join(failures)

    report = BackupReport(
        backup_id=backup_set.backup_id,
        started_at=started_at,
        finished_at=finished_at,
        actual_rpo_hours=round(actual_rpo_hours, 2),
        actual_rto_hours=round(actual_rto_hours, 2),
        rpo_target_hours=RPO_TARGET_HOURS,
        rto_target_hours=RTO_TARGET_HOURS,
        migration_check=migration_result,
        integrity_check=integrity_result,
        file_check=file_result,
        overall_passed=all_passed,
        failure_reason=failure_reason,
    )

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(_serialize_report(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    logger.info(
        "backup.verify.completed",
        backup_id=backup_set.backup_id,
        overall_passed=all_passed,
        rpo_hours=report.actual_rpo_hours,
        rto_hours=report.actual_rto_hours,
    )

    return report


# ── 内部辅助 ───────────────────────────────────────────────────────────────


def _generate_backup_id(now: datetime) -> str:
    """生成唯一 Backup ID.

    格式: ``backup-YYYYMMDD-HHMMSS-<8 hex>``，确保时间可读且全局唯一。
    """

    ts = now.strftime("%Y%m%d-%H%M%S")
    short_uuid = uuid4().hex[:8]
    return f"backup-{ts}-{short_uuid}"


async def _check_db_available(database_url: str) -> bool:
    """检查数据库是否可用 — SPEC 27.1: 备份失败可发现."""

    from sqlalchemy import text

    from app.infrastructure.db.engine import create_db_engine

    engine = create_db_engine(database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


def _run_pg_dump(
    bin_dir: Path | None,
    params: dict[str, str | int | None],
    output_path: Path,
) -> None:
    """执行 pg_dump 逻辑全量备份.

    使用 --format=plain（SQL 脚本），--no-owner --no-privileges
    确保恢复时不依赖原始角色定义。
    """

    pg_dump = _get_pg_executable(bin_dir, "pg_dump")
    env = _build_pg_env(params)

    result = subprocess.run(
        [
            pg_dump,
            "--format=plain",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(output_path),
            str(params["dbname"]),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        msg = f"pg_dump 失败（退出码 {result.returncode}）: {result.stderr[:500]}"
        raise BackupCreationError(msg)


def _run_psql_restore(
    bin_dir: Path | None,
    params: dict[str, str | int | None],
    dump_path: Path,
) -> None:
    """使用 psql 将 SQL 备份恢复到目标数据库."""

    psql = _get_pg_executable(bin_dir, "psql")
    env = _build_pg_env(params)

    result = subprocess.run(
        [
            psql,
            "-v",
            "ON_ERROR_STOP=1",
            "--quiet",
            "-f",
            str(dump_path),
            str(params["dbname"]),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        msg = f"psql 恢复失败（退出码 {result.returncode}）: {result.stderr[:500]}"
        raise BackupVerificationError(msg)


def _create_isolated_database(
    bin_dir: Path | None,
    server_params: dict[str, str | int | None],
    db_name: str,
) -> None:
    """在源服务器上创建隔离数据库."""

    psql = _get_pg_executable(bin_dir, "psql")
    # 连接到 'postgres' 管理数据库执行 CREATE DATABASE
    env = _build_pg_env(server_params)

    result = subprocess.run(
        [
            psql,
            "--quiet",
            "-d",
            "postgres",
            "-c",
            f'CREATE DATABASE "{db_name}"',
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        msg = f"创建隔离数据库失败: {result.stderr[:500]}"
        raise BackupVerificationError(msg)


def _drop_isolated_database(
    bin_dir: Path | None,
    server_params: dict[str, str | int | None],
    db_name: str,
) -> None:
    """删除隔离数据库."""

    psql = _get_pg_executable(bin_dir, "psql")
    env = _build_pg_env(server_params)

    result = subprocess.run(
        [
            psql,
            "--quiet",
            "-d",
            "postgres",
            "-c",
            f'DROP DATABASE IF EXISTS "{db_name}"',
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        logger.warning(
            "backup.verify.drop_failed",
            database=db_name,
            error=result.stderr[:200],
        )


def _build_pg_env(
    params: dict[str, str | int | None],
) -> dict[str, str]:
    """从连接参数构造 pg_dump/psql 环境变量字典."""

    env: dict[str, str] = {}
    # 继承 PATH（确保能找到 libpq 等）
    for key in ("PATH", "SYSTEMROOT", "PATHEXT", "WINDIR"):
        val = os.environ.get(key)
        if val:
            env[key] = val

    if params.get("host"):
        env["PGHOST"] = str(params["host"])
    if params.get("port"):
        env["PGPORT"] = str(params["port"])
    if params.get("user"):
        env["PGUSER"] = str(params["user"])
    if params.get("password"):
        env["PGPASSWORD"] = str(params["password"])
    return env


def _build_db_url(params: dict[str, str | int | None]) -> str:
    """从连接参数构造 SQLAlchemy URL."""

    user = params.get("user") or "apex"
    host = params.get("host") or "127.0.0.1"
    port = params.get("port") or 5432
    dbname = params.get("dbname") or "postgres"
    return f"postgresql+psycopg://{user}@{host}:{port}/{dbname}"


async def _build_manifest_from_db(
    database_url: str,
) -> list[FileManifestEntry]:
    """查询 READY 文件元数据并构建清单条目."""

    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.file.adapter import SqlAlchemyFileRepository
    from app.modules.file.models import FileStatus

    engine = create_db_engine(database_url)
    try:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            repo = SqlAlchemyFileRepository(uow.session)
            ready_files = await repo.list_by_status(FileStatus.READY)
        return build_file_entries(ready_files)
    finally:
        await engine.dispose()


async def _check_migration_version(
    iso_db_url: str,
    source_db_url: str,
) -> CheckResult:
    """检查隔离库的迁移版本是否与源库 head 一致 — SPEC 27.3."""

    from sqlalchemy import text

    from app.composition.modules import MODULE_VERSION_LOCATIONS
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.migrations import get_head_revision

    try:
        head_rev = get_head_revision(MODULE_VERSION_LOCATIONS)
    except Exception as exc:
        return CheckResult(passed=False, detail=f"无法获取 head revision: {exc}")

    engine = create_db_engine(iso_db_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version"),
            )
            row = result.fetchone()
            iso_rev = row[0] if row else ""
    except Exception as exc:
        return CheckResult(
            passed=False,
            detail=f"无法读取隔离库迁移版本: {exc}",
        )
    finally:
        await engine.dispose()

    if iso_rev == head_rev:
        return CheckResult(
            passed=True,
            detail=f"迁移版本一致: {iso_rev}",
        )
    return CheckResult(
        passed=False,
        detail=f"迁移版本不匹配: 恢复库={iso_rev}, head={head_rev}",
    )


async def _check_data_integrity(iso_db_url: str) -> CheckResult:
    """在隔离库上执行数据完整性检查 — SPEC 27.3."""

    from app.core.data_check import run_data_check
    from app.infrastructure.db.engine import create_db_engine
    from app.infrastructure.db.uow import SqlAlchemyUnitOfWork

    engine = create_db_engine(iso_db_url)
    try:
        uow = SqlAlchemyUnitOfWork(engine)
        async with uow:
            result = await run_data_check(uow.session)

        if result.healthy:
            return CheckResult(passed=True, detail="数据完整性检查通过")
        issues_summary = "; ".join(f"{i.check}:{i.detail}" for i in result.issues[:5])
        return CheckResult(
            passed=False,
            detail=f"发现 {len(result.issues)} 个问题: {issues_summary}",
        )
    except Exception as exc:
        return CheckResult(
            passed=False,
            detail=f"数据完整性检查异常: {exc}",
        )
    finally:
        await engine.dispose()


def _check_file_consistency(
    backup_dir: Path,
    backup_set: BackupSet,
) -> CheckResult:
    """验证备份集中文件的 SHA-256 与清单一致 — SPEC 27.3.

    SPEC 27.2: "恢复后可以检查文件记录与物理文件一致性"。
    """

    files_dir = backup_dir / "files"
    mismatches: list[str] = []

    for entry in backup_set.files:
        file_path = files_dir / entry.storage_name
        if not file_path.exists():
            mismatches.append(f"文件缺失: {entry.storage_name}")
            continue
        actual_hash = _compute_file_sha256(file_path)
        if actual_hash != entry.sha256:
            mismatches.append(
                f"哈希不匹配: {entry.storage_name}"
                f" (清单={entry.sha256[:16]}..., 实际={actual_hash[:16]}...)",
            )

    if not mismatches:
        return CheckResult(
            passed=True,
            detail=f"文件一致性检查通过（{len(backup_set.files)} 个文件）",
        )
    return CheckResult(
        passed=False,
        detail=f"文件一致性检查失败: {'; '.join(mismatches[:5])}",
    )


def _compute_file_sha256(path: Path) -> str:
    """计算文件的 SHA-256 摘要."""

    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def _serialize_report(report: BackupReport) -> dict[str, object]:
    """将演练报告序列化为可写 JSON 字典."""

    return {
        "backup_id": report.backup_id,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "actual_rpo_hours": report.actual_rpo_hours,
        "actual_rto_hours": report.actual_rto_hours,
        "rpo_target_hours": report.rpo_target_hours,
        "rto_target_hours": report.rto_target_hours,
        "checks": {
            "migration_version": {
                "passed": report.migration_check.passed,
                "detail": report.migration_check.detail,
            },
            "data_integrity": {
                "passed": report.integrity_check.passed,
                "detail": report.integrity_check.detail,
            },
            "file_consistency": {
                "passed": report.file_check.passed,
                "detail": report.file_check.detail,
            },
        },
        "overall_passed": report.overall_passed,
        "failure_reason": report.failure_reason,
    }
