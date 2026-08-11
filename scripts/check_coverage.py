"""覆盖率门槛校验脚本.

读取 ``coverage json`` 生成的覆盖率报告，分别判定语句覆盖率与分支覆盖率
是否达到阈值。SPEC 28.1 要求单元测试语句覆盖率不低于 85%、分支覆盖率
不低于 80%；后续门槛模块有更高要求时可在调用时覆盖阈值。

用法::

    # 先生成覆盖率 JSON
    uv run coverage json -o .generated/coverage.json

    # 使用默认门槛（语句 85%，分支 80%）
    uv run python scripts/check_coverage.py

    # 指定门槛
    uv run python scripts/check_coverage.py \\
        --statement-threshold 90 \\
        --branch-threshold 90

    # 限定文件范围（逗号分隔的 glob 模式，路径分隔符自动归一化）
    uv run python scripts/check_coverage.py \\
        --statements 90 --branches 90 \\
        --include "src/app/modules/identity/*,src/app/modules/auth/*" \\
        coverage.json
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="校验 coverage JSON 报告的语句与分支覆盖率门槛。",
    )
    parser.add_argument(
        "coverage_file",
        type=Path,
        nargs="?",
        default=Path(".generated/coverage.json"),
        help="coverage json 输出文件路径（默认 .generated/coverage.json）。",
    )
    parser.add_argument(
        "--coverage-file",
        dest="coverage_file_opt",
        type=Path,
        default=None,
        help="coverage json 输出文件路径（覆盖位置参数）。",
    )
    parser.add_argument(
        "--statement-threshold",
        "--statements",
        dest="statement_threshold",
        type=float,
        default=85.0,
        help="语句覆盖率门槛百分比（默认 85）。",
    )
    parser.add_argument(
        "--branch-threshold",
        "--branches",
        dest="branch_threshold",
        type=float,
        default=80.0,
        help="分支覆盖率门槛百分比（默认 80）。",
    )
    parser.add_argument(
        "--include",
        dest="include_patterns",
        type=str,
        default=None,
        help=(
            "逗号分隔的 glob 模式，仅统计匹配文件的聚合覆盖率。"
            "路径分隔符自动归一化（正反斜杠均可匹配）。"
        ),
    )
    return parser.parse_args()


def _load_coverage(coverage_file: Path) -> dict[str, object]:
    """读取并解析 coverage JSON 文件。

    参数:
        coverage_file: coverage json 文件路径。

    返回:
        coverage 数据字典。
    """

    if not coverage_file.exists():
        raise FileNotFoundError(
            f"覆盖率文件不存在: {coverage_file}。"
            "请先运行 'uv run coverage json -o .generated/coverage.json'。",
        )

    return json.loads(coverage_file.read_text(encoding="utf-8"))


def _normalize_path(path: str) -> str:
    """将路径中的反斜杠归一化为正斜杠，便于跨平台 glob 匹配。"""

    return path.replace("\\", "/")


def _matches_any(path: str, patterns: list[str]) -> bool:
    """检查路径是否匹配任一 glob 模式（路径分隔符已归一化）。"""

    normalized = _normalize_path(path)
    return any(fnmatch.fnmatch(normalized, pat) for pat in patterns)


def _extract_percentages(
    coverage_data: dict[str, object],
) -> tuple[float, float]:
    """从 coverage JSON 的全局 totals 中提取语句与分支覆盖率百分比。

    coverage 7.x 的 ``totals`` 字段使用 ``percent_covered``（综合覆盖率）
    与 ``percent_branches_covered``（分支覆盖率）。旧版本使用
    ``percent_covered_branches``。函数兼容两种键名。

    参数:
        coverage_data: coverage JSON 解析后的字典。

    返回:
        (语句覆盖率, 分支覆盖率) 二元组。
    """

    totals = coverage_data.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("覆盖率 JSON 中缺少 'totals' 字段或格式不正确。")

    statement_pct = float(totals.get("percent_covered", 0.0))
    # coverage 7.x 使用 percent_branches_covered，旧版本使用 percent_covered_branches
    branch_raw = totals.get("percent_branches_covered")
    if branch_raw is None:
        branch_raw = totals.get("percent_covered_branches", 0.0)
    branch_pct = float(branch_raw)
    return statement_pct, branch_pct


def _extract_percentages_for_files(
    coverage_data: dict[str, object],
    include_patterns: list[str],
) -> tuple[float, float]:
    """对匹配 ``--include`` 模式的文件计算聚合覆盖率百分比。

    遍历 ``files`` 字典，按 glob 模式筛选文件，累加语句与分支的
    已覆盖数与总数，计算聚合百分比。路径分隔符自动归一化。

    参数:
        coverage_data: coverage JSON 解析后的字典。
        include_patterns: 归一化后的 glob 模式列表。

    返回:
        (语句覆盖率, 分支覆盖率) 二元组。
    """

    files = coverage_data.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("覆盖率 JSON 中缺少 'files' 字段或为空。")

    total_statements = 0
    covered_statements = 0
    total_branches = 0
    covered_branches = 0

    for fpath, fdata in files.items():
        if not _matches_any(fpath, include_patterns):
            continue
        if not isinstance(fdata, dict):
            continue
        summary = fdata.get("summary")
        if not isinstance(summary, dict):
            continue
        total_statements += int(summary.get("num_statements", 0))
        covered_statements += int(summary.get("covered_lines", 0))
        total_branches += int(summary.get("num_branches", 0))
        covered_branches += int(summary.get("covered_branches", 0))

    if total_statements == 0:
        statement_pct = 100.0
    else:
        statement_pct = covered_statements / total_statements * 100.0

    if total_branches == 0:
        branch_pct = 100.0
    else:
        branch_pct = covered_branches / total_branches * 100.0

    return statement_pct, branch_pct


def main() -> int:
    """脚本主入口：校验覆盖率门槛。

    返回:
        0 表示全部达标，1 表示存在不达标项或读取失败。
    """

    args = _parse_args()

    coverage_file = args.coverage_file_opt or args.coverage_file

    try:
        coverage_data = _load_coverage(coverage_file)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    # 如果指定了 --include，仅统计匹配文件的聚合覆盖率
    if args.include_patterns:
        patterns = [
            _normalize_path(p.strip())
            for p in args.include_patterns.split(",")
            if p.strip()
        ]
        statement_pct, branch_pct = _extract_percentages_for_files(
            coverage_data,
            patterns,
        )
    else:
        statement_pct, branch_pct = _extract_percentages(coverage_data)

    ok = True
    if statement_pct < args.statement_threshold:
        print(
            f"语句覆盖率 {statement_pct:.2f}% 低于门槛 {args.statement_threshold:.2f}%",
            file=sys.stderr,
        )
        ok = False

    if branch_pct < args.branch_threshold:
        print(
            f"分支覆盖率 {branch_pct:.2f}% 低于门槛 {args.branch_threshold:.2f}%",
            file=sys.stderr,
        )
        ok = False

    if ok:
        print(
            f"覆盖率达标：语句 {statement_pct:.2f}%，分支 {branch_pct:.2f}%",
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
