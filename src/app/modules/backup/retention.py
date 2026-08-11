"""备份滚动保留策略 — SPEC 27.1.

SPEC 27.1: "备份文件至少保留最近 7 个日备份和最近 4 个周备份"。

策略:
  1. 日备份保留: 按日历日期分组，每个日期保留最新一个备份，
     保留最近 ``daily_retention`` 个日期的备份。
  2. 周备份保留: 按 ISO 周分组，每周保留最新一个备份，
     保留最近 ``weekly_retention`` 个周的备份。
  3. 两个集合的并集为保留集，其余备份集标记为待删除。

此模块只做策略计算（纯函数），不执行文件系统删除。
调用方负责根据返回的删除列表执行实际清理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.modules.backup.manifest import read_manifest

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class RetentionResult:
    """保留策略计算结果.

    属性:
        keep_dirs:   应保留的备份集目录列表。
        delete_dirs: 应删除的备份集目录列表。
    """

    keep_dirs: list[Path]
    delete_dirs: list[Path]


def apply_retention(
    output_dir: Path,
    *,
    daily_retention: int,
    weekly_retention: int,
) -> RetentionResult:
    """计算滚动保留策略下的保留与删除列表.

    参数:
        output_dir: 包含多个备份集的输出目录。
        daily_retention: 日备份保留数量（最近 N 个日历日期）。
        weekly_retention: 周备份保留数量（最近 N 个 ISO 周）。

    返回:
        RetentionResult，包含应保留和应删除的目录列表。
    """

    if not output_dir.exists():
        return RetentionResult(keep_dirs=[], delete_dirs=[])

    # 收集所有备份集 (created_at, dir)。
    backups: list[tuple[datetime, Path]] = []
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.exists():
            continue
        backup = read_manifest(child)
        backups.append((backup.created_at, child))

    if not backups:
        return RetentionResult(keep_dirs=[], delete_dirs=[])

    # 按时间降序排列（最新在前）。
    backups.sort(key=lambda pair: pair[0], reverse=True)

    keep_set: set[Path] = set()

    # ── 日备份保留: 最近 N 个日历日期，每个日期保留最新 ──
    seen_dates: list[str] = []
    for created_at, dir_path in backups:
        date_key = created_at.astimezone(UTC).strftime("%Y-%m-%d")
        if date_key not in seen_dates:
            seen_dates.append(date_key)
            if len(seen_dates) <= daily_retention:
                keep_set.add(dir_path)

    # ── 周备份保留: 最近 N 个 ISO 周，每周保留最新 ──
    seen_weeks: list[str] = []
    for created_at, dir_path in backups:
        iso_year, iso_week, _ = created_at.astimezone(UTC).isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        if week_key not in seen_weeks:
            seen_weeks.append(week_key)
            if len(seen_weeks) <= weekly_retention:
                keep_set.add(dir_path)

    keep_dirs = [d for _, d in backups if d in keep_set]
    delete_dirs = [d for _, d in backups if d not in keep_set]

    return RetentionResult(keep_dirs=keep_dirs, delete_dirs=delete_dirs)


def format_retention_report(result: RetentionResult) -> str:
    """格式化保留策略报告为可读文本."""

    lines: list[str] = [
        "备份保留策略报告",
        "=" * 50,
        f"  保留备份集: {len(result.keep_dirs)}",
        f"  待删除备份集: {len(result.delete_dirs)}",
    ]
    if result.keep_dirs:
        lines.append("  保留:")
        for d in result.keep_dirs:
            lines.append(f"    + {d.name}")
    if result.delete_dirs:
        lines.append("  待删除:")
        for d in result.delete_dirs:
            lines.append(f"    - {d.name}")
    lines.append("=" * 50)
    return "\n".join(lines)
