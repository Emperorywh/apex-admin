#!/usr/bin/env python3
"""Copier 模板生成实例验证线束 — SPEC 30.3 / 34.4.

单一有界入口，负责：
  1. 前置检查（Git 工作树干净、Copier 可用）
  2. 生成临时项目（copier copy --defaults）
  3. 就绪检查（uv lock --check、uv sync --frozen）
  4. 数据库迁移（alembic upgrade head）
  5. 指定门槛测试（pytest -m "g1 or g2 or g3"）
  6. 标识残留检查（grep apex-admin / APEX_ / urn:apex）
  7. 清理临时项目

使用方式::

    uv run python scripts/verify_generated.py --gate g3

前置条件:
  - Git 工作树干净（Copier 从本地路径复制时使用 git 已提交状态）
  - Copier 已安装（pyproject.toml [project.optional-dependencies] template）
  - 数据库供应链可用（Testcontainers 或本地二进制）

本脚本是唯一有界验证入口：它在同一控制流内完成生成、验证与清理，
不在后台遗留任何进程或临时目录。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── 常量 ──────────────────────────────────────────────────────────────────

# 默认答案的 config_prefix（与 copier.yml 中的默认值一致）
_DEFAULT_CONFIG_PREFIX = "APP_"

# 默认答案的 project_slug
_DEFAULT_PROJECT_SLUG = "my-backend"

# 默认答案的 package_name
_DEFAULT_PACKAGE_NAME = "app"

# 标识残留检查模式
_IDENTITY_PATTERNS: list[str] = [
    "apex-admin",
    "APEX_",
    "urn:apex",
]

# grep 检查时排除的文件/目录
_GREP_EXCLUDES: list[str] = [
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".copier-answers.yml",
    "_copier_postprocess.py",
]

# GATE 到 pytest marker 的映射
_GATE_MARKERS: dict[str, str] = {
    "g1": "g1",
    "g2": "g2",
    "g3": "g3",
    "g123": "g1 or g2 or g3",
    # g4: g1-g3 全量 + 本地可运行 g4 子集（排除需要 Docker 全栈的 integration 测试）
    "g4": "(g1 or g2 or g3) or (g4 and not integration)",
}


# ── 工具函数 ──────────────────────────────────────────────────────────────


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 3600,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """运行命令，统一输出格式。"""

    cwd_str = str(cwd) if cwd else os.getcwd()
    print(f"\n{'=' * 70}")
    print(f"[RUN] {' '.join(cmd)}")
    print(f"[CWD] {cwd_str}")
    print(f"{'=' * 70}")

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    result = subprocess.run(
        cmd,
        cwd=cwd_str,
        env=merged_env,
        capture_output=False,
        timeout=timeout,
    )

    if check and result.returncode != 0:
        print(f"\n[FAIL] 退出码 {result.returncode}")
    else:
        print(f"\n[OK] 退出码 {result.returncode}")

    return result


def _check_git_clean(template_root: Path) -> bool:
    """检查 Git 工作树是否干净。"""

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(template_root),
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print("[ERROR] Git 工作树不干净，Copier 从本地路径复制时使用 git 已提交状态。")
        print("请先提交或暂存所有更改：")
        print(result.stdout)
        return False
    return True


def _ensure_postgres_ready(template_root: Path) -> str | None:
    """确保 PostgreSQL 可用，返回测试数据库 URL。

    尝试使用模板仓库已供应的 PostgreSQL 二进制启动一个开发实例。
    如果已有实例在运行（端口 55432），直接复用。
    返回 None 表示无法供应。
    """

    # 检查 Docker 是否可用
    try:
        docker_result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        docker_available = docker_result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        docker_available = False

    if docker_available:
        print("[INFO] Docker 可用，生成项目将通过 Testcontainers 自动供应数据库")
        return None  # 让 conftest.py 自己处理

    # 检查本地二进制是否已供应
    runtime_dir = template_root / ".runtime"
    if not runtime_dir.exists():
        print("[INFO] 尝试供应本地 PostgreSQL 二进制...")
        result = _run(
            [sys.executable, "scripts/dev_db.py", "ensure"],
            cwd=template_root,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            print("[WARN] 本地 PostgreSQL 二进制供应失败")
            return None

    # 检查开发实例是否已在运行
    import socket

    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    if _port_in_use(55432):
        print("[INFO] PostgreSQL 开发实例已在端口 55432 运行")
        return "postgresql+psycopg://apex@127.0.0.1:55432/postgres"

    # 启动开发实例
    print("[INFO] 启动 PostgreSQL 开发实例...")
    _run(
        [sys.executable, "scripts/dev_db.py", "start"],
        cwd=template_root,
        timeout=30,
        check=False,
    )

    # 等待就绪（带截止时间的条件轮询）
    for _ in range(30):
        if _port_in_use(55432):
            print("[INFO] PostgreSQL 开发实例就绪")
            return "postgresql+psycopg://apex@127.0.0.1:55432/postgres"
        time.sleep(1)

    print("[WARN] PostgreSQL 开发实例启动超时")
    return None


def _check_identity_residue(project_dir: Path) -> bool:
    """检查生成项目中是否存在基座标识残留。

    返回 True 表示无残留（通过），False 表示有残留（失败）。
    使用纯 Python 遍历文件，不依赖系统 grep。
    """

    print(f"\n{'=' * 70}")
    print("[CHECK] 标识残留检查")
    print(f"{'=' * 70}")

    all_clean = True

    for pattern in _IDENTITY_PATTERNS:
        matches: list[str] = []
        for dirpath, dirnames, filenames in os.walk(project_dir):
            dirnames[:] = [d for d in dirnames if d not in _GREP_EXCLUDES]
            for filename in filenames:
                if filename in _GREP_EXCLUDES:
                    continue
                filepath = Path(dirpath) / filename
                try:
                    content = filepath.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for i, line in enumerate(content.splitlines(), 1):
                    if pattern in line:
                        rel = filepath.relative_to(project_dir)
                        matches.append(f"{rel}:{i}: {line.strip()[:120]}")
                        if len(matches) >= 50:
                            break
                if len(matches) >= 50:
                    break
            if len(matches) >= 50:
                break

        if matches:
            print(f"\n[FAIL] 发现标识残留 '{pattern}'：")
            for line in matches[:20]:
                print(f"  {line}")
            if len(matches) > 20:
                print(f"  ... 共 {len(matches)} 处匹配")
            all_clean = False
        else:
            print(f"[OK] 无 '{pattern}' 残留")

    return all_clean


def _check_answers_file(project_dir: Path) -> bool:
    """检查 .copier-answers.yml 是否记录了答案与模板版本。"""

    answers_file = project_dir / ".copier-answers.yml"
    if not answers_file.exists():
        print("[FAIL] .copier-answers.yml 不存在")
        return False

    content = answers_file.read_text(encoding="utf-8")
    required_keys = ["_commit", "_src_path", "project_name", "project_slug"]
    missing = [k for k in required_keys if k not in content]
    if missing:
        print(f"[FAIL] .copier-answers.yml 缺少键: {missing}")
        return False

    print("[OK] .copier-answers.yml 记录完整（含 _commit、_src_path 和答案）")
    return True


# ── 主流程 ────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copier 模板生成实例验证线束",
    )
    parser.add_argument(
        "--gate",
        choices=list(_GATE_MARKERS.keys()),
        default="g3",
        help="验证门槛（默认 g3）",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留生成的临时项目（调试用）",
    )
    args = parser.parse_args()

    template_root = Path(__file__).resolve().parent.parent
    print(f"[INFO] 模板根目录: {template_root}")

    # ── 前置检查 ──────────────────────────────────────────────────────

    print("\n─── 前置检查 ───")

    if not _check_git_clean(template_root):
        return 1

    # 确保 Copier 可用
    copier_check = subprocess.run(
        [sys.executable, "-m", "copier", "--version"],
        capture_output=True,
        text=True,
    )
    if copier_check.returncode != 0:
        print("[ERROR] Copier 不可用，请安装: uv pip install copier")
        return 1
    print(f"[OK] Copier {copier_check.stdout.strip()}")

    # 确保 PostgreSQL 供应链可用
    db_url = _ensure_postgres_ready(template_root)

    # ── 生成临时项目 ──────────────────────────────────────────────────

    print("\n─── 生成临时项目 ───")

    # 使用 tempfile 在 finally 路径中清理
    tmp_parent = Path(tempfile.mkdtemp(prefix="copier_verify_"))
    project_dir = tmp_parent / _DEFAULT_PROJECT_SLUG

    try:
        # copier copy --defaults 使用全部默认答案
        # --vcs-ref HEAD 确保使用已提交状态
        copier_cmd = [
            sys.executable,
            "-m",
            "copier",
            "copy",
            "--defaults",
            "--vcs-ref",
            "HEAD",
            "--trust",
            str(template_root),
            str(project_dir),
        ]

        result = _run(copier_cmd, cwd=template_root, timeout=120, check=False)
        if result.returncode != 0:
            print("[FAIL] copier copy 失败")
            return 1

        # 检查 answers 文件
        if not _check_answers_file(project_dir):
            return 1

        # ── 就绪检查：uv lock --check ────────────────────────────────

        print("\n─── 就绪检查：uv lock --check ───")

        result = _run(
            ["uv", "lock", "--check"],
            cwd=project_dir,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            print("[FAIL] uv lock --check 失败")
            return 1

        # ── 就绪检查：uv sync --frozen ───────────────────────────────

        print("\n─── 就绪检查：uv sync --frozen ───")

        result = _run(
            ["uv", "sync", "--frozen"],
            cwd=project_dir,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            print("[FAIL] uv sync --frozen 失败")
            return 1

        # ── 代码格式化（身份替换可能改变行长度）─────────────────────────

        print("\n─── 代码格式化 ───")

        _run(
            ["uv", "run", "ruff", "format", "."],
            cwd=project_dir,
            timeout=60,
            check=False,
        )

        # ── 静态检查 ─────────────────────────────────────────────────

        print("\n─── 静态检查 ───")

        checks = [
            (["uv", "run", "ruff", "check", "."], "ruff check"),
            (["uv", "run", "ruff", "format", "--check", "."], "ruff format check"),
            (["uv", "run", "mypy", "--strict", "src"], "mypy strict"),
            (["uv", "run", "lint-imports"], "lint-imports"),
        ]

        for cmd, name in checks:
            result = _run(cmd, cwd=project_dir, timeout=120, check=False)
            if result.returncode != 0:
                print(f"[FAIL] {name} 失败")
                return 1

        # ── 模块注册校验 ─────────────────────────────────────────────

        print("\n─── 模块注册校验 ───")

        cli_module = f"{_DEFAULT_PACKAGE_NAME}.cli"
        result = _run(
            ["uv", "run", "python", "-m", cli_module, "modules", "validate"],
            cwd=project_dir,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            print("[FAIL] modules validate 失败")
            return 1

        # ── 数据库迁移 ───────────────────────────────────────────────

        print("\n─── 数据库迁移 ───")

        # 设置测试数据库 URL 环境变量
        test_env: dict[str, str] = {}
        if db_url:
            test_env[f"{_DEFAULT_CONFIG_PREFIX}TEST_DATABASE_URL"] = db_url
            # alembic 读取 DATABASE_URL，需指向实际 PostgreSQL 实例
            # （生成项目的默认 DATABASE_URL 使用模板化后的用户名，
            # 与本地 PostgreSQL 实例的实际用户不同）
            test_env[f"{_DEFAULT_CONFIG_PREFIX}DATABASE_URL"] = db_url
            test_env[f"{_DEFAULT_CONFIG_PREFIX}ENVIRONMENT"] = "testing"

        result = _run(
            ["uv", "run", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=project_dir,
            env=test_env,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            print("[FAIL] 数据库迁移失败")
            return 1

        result = _run(
            ["uv", "run", "alembic", "-c", "alembic.ini", "heads"],
            cwd=project_dir,
            env=test_env,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            print("[FAIL] alembic heads 检查失败")
            return 1

        # ── 门槛测试 ─────────────────────────────────────────────────

        marker = _GATE_MARKERS[args.gate]
        print(f"\n─── 门槛测试：pytest -m {marker} ───")

        result = _run(
            ["uv", "run", "pytest", "-x", "-m", marker],
            cwd=project_dir,
            env=test_env,
            timeout=3600,
            check=False,
        )
        if result.returncode != 0:
            print("[FAIL] pytest 失败")
            return 1

        # ── 标识残留检查 ─────────────────────────────────────────────

        print("\n─── 标识残留检查 ───")

        if not _check_identity_residue(project_dir):
            return 1

        # ── 全部通过 ─────────────────────────────────────────────────

        print(f"\n{'=' * 70}")
        print("[SUCCESS] 全部验证通过！")
        print(f"{'=' * 70}")
        return 0

    finally:
        # ── 清理 ─────────────────────────────────────────────────────
        if args.keep:
            print(f"\n[INFO] 保留生成项目: {project_dir}")
        else:
            print(f"\n[INFO] 清理临时目录: {tmp_parent}")
            shutil.rmtree(tmp_parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
