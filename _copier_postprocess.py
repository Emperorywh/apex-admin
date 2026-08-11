#!/usr/bin/env python3
"""Copier 生成后身份替换脚本.

由 copier.yml _tasks 调用，在生成项目根目录执行。
答案通过命令行参数传入（Copier 在 task 执行后才写入 .copier-answers.yml）。
对全部文本文件做确定性身份替换，然后删除自身。

替换范围:
  - 配置前缀: APEX_ → {config_prefix}
  - 项目标识: apex-admin → {project_slug}
  - URN 命名空间: urn:apex: → {urn_namespace}:
  - Prometheus 指标名、Cookie 名、权限属性、Logger 名、UUID 命名空间等
  - 数据库默认用户名/库名
  - uv.lock 根包名
  - 文档中的项目显示名
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ── 解析命令行参数 ────────────────────────────────────────────────────────


def _parse_args() -> dict[str, str]:
    """从命令行参数读取 Copier 答案。

    参数顺序与 copier.yml _tasks Jinja2 渲染一致：
    project_name project_slug package_name urn_namespace config_prefix
    _commit _src_path ext_enabled
    """

    if len(sys.argv) < 8:
        print(
            "[postprocess] 用法: _copier_postprocess.py "
            "<project_name> <project_slug> <package_name> "
            "<urn_namespace> <config_prefix> "
            "<_commit> <_src_path> [ext_enabled]",
            file=sys.stderr,
        )
        sys.exit(1)

    return {
        "project_name": sys.argv[1],
        "project_slug": sys.argv[2],
        "package_name": sys.argv[3],
        "urn_namespace": sys.argv[4],
        "config_prefix": sys.argv[5],
        "_commit": sys.argv[6],
        "_src_path": sys.argv[7],
        "ext_enabled": sys.argv[8] if len(sys.argv) > 8 else "false",
    }


def _write_answers_file(answers: dict[str, str]) -> None:
    """写入 .copier-answers.yml（Copier 在某些环境下无法自动生成）。"""

    content = (
        "# 此文件由 Copier 生成，记录项目答案与模板来源版本。\n"
        "# 请勿手动修改——copier update 依赖此文件计算差异。\n"
        f"_commit: {answers['_commit']}\n"
        f"_src_path: {answers['_src_path']}\n"
        f"project_name: {answers['project_name']}\n"
        f"project_slug: {answers['project_slug']}\n"
        f"package_name: {answers['package_name']}\n"
        f"urn_namespace: {answers['urn_namespace']}\n"
        f"config_prefix: {answers['config_prefix']}\n"
        f"ext_enabled: {answers['ext_enabled']}\n"
    )
    Path(".copier-answers.yml").write_text(content, encoding="utf-8")
    print("[postprocess] .copier-answers.yml 已写入")


# ── 文本文件判定 ──────────────────────────────────────────────────────────

# 跳过的目录（生成项目中的运行时产物）
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".runtime",
        ".import_linter_cache",
        ".generated",
        "node_modules",
    }
)

# 跳过的文件（不应修改）
_SKIP_FILES: frozenset[str] = frozenset(
    {
        ".copier-answers.yml",
        "_copier_postprocess.py",
    }
)

# 文件扩展名白名单（只处理文本文件）
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".toml",
        ".cfg",
        ".ini",
        ".yml",
        ".yaml",
        ".json",
        ".md",
        ".rst",
        ".txt",
        ".conf",
        ".env",
        ".example",
        ".sh",
        ".bat",
        ".ps1",
        ".dockerfile",
        "",
        ".gitignore",
        ".dockerignore",
    }
)


def _is_text_file(path: Path) -> bool:
    """判断文件是否为应处理的文本文件。"""

    if path.name in _SKIP_FILES:
        return False

    # Dockerfile 和 Makefile 没有扩展名但需要处理
    if path.name in {"Dockerfile", "Makefile", ".env.example", ".env"}:
        return True

    ext = path.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return True

    # 尝试 UTF-8 解码判断
    try:
        path.read_text(encoding="utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


# ── 文本迭代器 ────────────────────────────────────────────────────────────


def _iter_text_files(root: Path) -> list[Path]:
    """遍历根目录下所有应处理的文本文件。"""

    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地修改 dirnames 以跳过特定目录
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            filepath = Path(dirpath) / filename
            if _is_text_file(filepath):
                files.append(filepath)
    return files


# ── 替换引擎 ──────────────────────────────────────────────────────────────


def _build_replacements(answers: dict[str, str]) -> list[tuple[str, str]]:
    """构建确定性替换规则列表（顺序敏感）。"""

    project_slug = answers["project_slug"]
    urn_ns = answers["urn_namespace"]
    config_prefix = answers["config_prefix"]
    project_name = answers["project_name"]

    # project_slug 的下划线形式（用于 Python 标识符）
    pu = project_slug.replace("-", "_")

    rules: list[tuple[str, str]] = []

    # ── 1. 配置前缀 APEX_ → config_prefix ────────────────────────────
    # 覆盖 env_prefix、环境变量名、错误消息、.env 文件、CI 配置等
    rules.append(("APEX_", config_prefix))

    # ── 2. 项目名 apex-admin → project_slug ──────────────────────────
    # 覆盖 pyproject.toml name、Docker 镜像名、README 等
    rules.append(("apex-admin", project_slug))

    # ── 3. URN 命名空间 urn:apex: → urn_namespace: ───────────────────
    # 覆盖 exception_handlers.py 和测试断言
    rules.append(("urn:apex:", f"{urn_ns}:"))

    # ── 4. 文档/注释中的显示名 Apex Admin → project_name ────────────
    rules.append(("Apex Admin", project_name))
    rules.append(("Apex admin", project_name))

    # ── 5. Prometheus 指标名（lowercase apex_ 前缀）──────────────────
    rules.append(("apex_http_requests_total", f"{pu}_http_requests_total"))
    rules.append(
        (
            "apex_http_request_errors_total",
            f"{pu}_http_request_errors_total",
        )
    )
    rules.append(
        (
            "apex_http_request_duration_seconds",
            f"{pu}_http_request_duration_seconds",
        )
    )
    rules.append(
        (
            "apex_db_pool_checked_out_connections",
            f"{pu}_db_pool_checked_out_connections",
        )
    )
    rules.append(("_apex_query_start_time", f"_{pu}_query_start_time"))

    # ── 6. Cookie 名 ─────────────────────────────────────────────────
    rules.append(("__Host-apex_refresh", f"__Host-{pu}_refresh"))
    # OpenAPI 快照中 cookie 参数标题（FastAPI 从 cookie 名自动生成）
    rules.append(("Apex Refresh", f"{project_name} Refresh"))

    # ── 7. 权限标记属性 ──────────────────────────────────────────────
    rules.append(("__apex_permission__", f"__{pu}_permission__"))

    # ── 8. Logger 名称 ───────────────────────────────────────────────
    rules.append(('"apex.security"', f'"{pu}.security"'))
    rules.append(('"apex.ops.file"', f'"{pu}.ops.file"'))
    rules.append(("'apex.security'", f"'{pu}.security'"))
    rules.append(("'apex.ops.file'", f"'{pu}.ops.file'"))

    # ── 9. Alembic 配置键 ────────────────────────────────────────────
    rules.append(("apex.url_explicitly_set", f"{pu}.url_explicitly_set"))

    # ── 10. UUID 命名空间（确定性种子）────────────────────────────────
    rules.append(('"apex:dict:', f'"{pu}:dict:'))
    rules.append(('"apex:menu:', f'"{pu}:menu:'))
    rules.append(('"apex:rbac:', f'"{pu}:rbac:'))

    # ── 11. Nginx upstream 名称 ──────────────────────────────────────
    rules.append(("apex_api", f"{pu}_api"))

    # ── 12. 数据库默认用户名/库名 ────────────────────────────────────
    # config.py DATABASE_URL 默认值、alembic.ini、compose.yaml、dev_db.py
    rules.append(("postgresql+psycopg://apex@", f"postgresql+psycopg://{pu}@"))
    rules.append(("postgresql+psycopg://apex:", f"postgresql+psycopg://{pu}:"))
    rules.append(("POSTGRES_USER: apex", f"POSTGRES_USER: {pu}"))
    rules.append(("POSTGRES_DB: apex", f"POSTGRES_DB: {pu}"))
    rules.append(("pg_isready -U apex", f"pg_isready -U {pu}"))
    rules.append(("PGUSER: apex", f"PGUSER: {pu}"))
    rules.append(("PGDATABASE: apex", f"PGDATABASE: {pu}"))
    rules.append(('PG_SUPERUSER: str = "apex"', f'PG_SUPERUSER: str = "{pu}"'))
    rules.append(('or "apex"', f'or "{pu}"'))

    # ── 13. 测试专用标识 ─────────────────────────────────────────────
    rules.append(("apex_verify_", f"{pu}_verify_"))
    rules.append(("apex_test_mig_head", f"{pu}_test_mig_head"))
    rules.append(("apex_test_pg_", f"{pu}_test_pg_"))

    # ── 14. Docker OCI 标签中的 GitHub 源 ────────────────────────────
    rules.append(("github.com/apex/apex-admin", f"github.com/{pu}/{project_slug}"))

    # ── 15. compose.yaml 注释中的文件路径引用 ────────────────────────
    # "deploy/nginx/apex.conf" → "deploy/nginx/{project_slug}.conf"
    rules.append(("nginx/apex.conf", f"nginx/{project_slug}.conf"))

    # ── 16. __init__.py 顶层文档字符串中的项目名 ─────────────────────
    rules.append(("Apex Admin —", f"{project_name} —"))

    return rules


def _apply_replacements(files: list[Path], rules: list[tuple[str, str]]) -> int:
    """对所有文件应用替换规则，返回修改的文件数。"""

    changed = 0
    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        original = content
        for old, new in rules:
            content = content.replace(old, new)

        if content != original:
            filepath.write_text(content, encoding="utf-8")
            changed += 1

    return changed


# ── 文件重命名 ────────────────────────────────────────────────────────────


def _rename_nginx_conf(project_slug: str) -> None:
    """重命名 deploy/nginx/apex.conf → deploy/nginx/{project_slug}.conf。"""

    old_path = Path("deploy/nginx/apex.conf")
    if old_path.exists():
        new_path = Path(f"deploy/nginx/{project_slug}.conf")
        shutil.move(str(old_path), str(new_path))
        print(f"[postprocess] 重命名 {old_path} → {new_path}")


def _rename_package_dir(package_name: str) -> None:
    """如果 package_name != 'app'，重命名 src/app → src/{package_name}。"""

    if package_name == "app":
        return

    old_dir = Path("src/app")
    new_dir = Path(f"src/{package_name}")
    if old_dir.exists():
        shutil.move(str(old_dir), str(new_dir))
        print(f"[postprocess] 重命名 {old_dir} → {new_dir}")


def _replace_package_imports(files: list[Path], package_name: str) -> None:
    """如果 package_name != 'app'，替换所有 from app. / import app 导入。"""

    if package_name == "app":
        return

    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        original = content
        content = content.replace("from app.", f"from {package_name}.")
        content = content.replace("import app\n", f"import {package_name}\n")
        content = content.replace("import app.", f"import {package_name}.")
        # 配置文件中的包名引用
        content = content.replace('"app"', f'"{package_name}"')
        content = content.replace("=app", f"={package_name}")
        # uvicorn 启动目标
        content = content.replace(
            "app.main:create_app",
            f"{package_name}.main:create_app",
        )
        content = content.replace('"app.cli"', f'"{package_name}.cli"')

        if content != original:
            filepath.write_text(content, encoding="utf-8")


# ── uv.lock 根包名替换 ────────────────────────────────────────────────────


def _patch_uv_lock(project_slug: str) -> None:
    """替换 uv.lock 中的根包名（spike 验证此方案可行，见 ADR-0004）。"""

    lock_file = Path("uv.lock")
    if not lock_file.exists():
        return

    content = lock_file.read_text(encoding="utf-8")
    # uv.lock 中根包名以 name = "apex-admin" 形式出现（仅一处）
    content = content.replace('name = "apex-admin"', f'name = "{project_slug}"')
    lock_file.write_text(content, encoding="utf-8")
    print(f"[postprocess] uv.lock 根包名替换为 {project_slug}")


# ── EXT 模块处理 ──────────────────────────────────────────────────────────


def _handle_ext(answers: dict[str, str]) -> None:
    """EXT 开关机制预留 — 当前无可用 EXT 模块，仅记录状态。"""

    ext_enabled = answers.get("ext_enabled", "false")
    if ext_enabled in ("true", "True", "yes"):
        print("[postprocess] EXT 模块已启用（当前无可用模块，机制预留）")
    else:
        print("[postprocess] EXT 模块未启用")


# ── 主入口 ────────────────────────────────────────────────────────────────


def main() -> None:
    answers = _parse_args()
    project_slug = answers["project_slug"]
    package_name = answers["package_name"]

    print(
        f"[postprocess] 开始身份替换："
        f"project_slug={project_slug}, package_name={package_name}"
    )

    rules = _build_replacements(answers)
    root = Path(".")
    files = _iter_text_files(root)

    # 文本替换
    changed = _apply_replacements(files, rules)
    print(f"[postprocess] 身份替换完成，修改 {changed} 个文件")

    # 包导入替换（仅在 package_name != app 时生效）
    _replace_package_imports(files, package_name)

    # uv.lock 根包名替换
    _patch_uv_lock(project_slug)

    # 文件重命名
    _rename_nginx_conf(project_slug)
    _rename_package_dir(package_name)

    # EXT 处理
    _handle_ext(answers)

    # 写入 .copier-answers.yml
    _write_answers_file(answers)

    print("[postprocess] 身份替换完成")


if __name__ == "__main__":
    main()
