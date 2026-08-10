"""Router 架构边界测试 — SPEC 5.6 / 34.1.

覆盖验收标准:
  - Router 无法解析或导入 AsyncSession 与 Repository（import 静态扫描）。

SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、Repository
或提交接口"。

此测试通过 AST 静态扫描 Router 模块的源文件，验证模块级导入不包含
``sqlalchemy.ext.asyncio``（AsyncSession）或任何 Repository 类型。
Router 只能导入 Use Case、Schema 和框架依赖。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _PROJECT_ROOT / "src" / "app"


def _find_router_files() -> list[Path]:
    """查找所有 Router 源文件.

    Router 文件包括:
      - ``src/app/api/*.py``（API 层路由）
      - ``src/app/modules/*/router.py``（业务模块路由）
    """

    files: list[Path] = []

    # API 层路由文件
    api_dir = _SRC_DIR / "api"
    if api_dir.exists():
        for py_file in api_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            files.append(py_file)

    # 业务模块路由文件
    modules_dir = _SRC_DIR / "modules"
    if modules_dir.exists():
        for router_file in modules_dir.glob("*/router.py"):
            files.append(router_file)

    return files


def _get_forbidden_imports(tree: ast.AST) -> list[str]:
    """检查 AST 中的模块级导入是否包含禁止的类型.

    禁止 Router 模块导入:
      - ``sqlalchemy.ext.asyncio``（AsyncSession 等 ORM 异步类型）
      - 任何名称包含 ``Repository`` 的导入（Repository Port/Adapter）

    返回违规导入的描述列表。
    """

    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # 禁止导入 sqlalchemy.ext.asyncio（AsyncSession 等）
            if module.startswith("sqlalchemy.ext.asyncio"):
                names = [alias.name for alias in node.names]
                violations.append(
                    f"line {node.lineno}: from {module} import {', '.join(names)}",
                )
            # 禁止导入 Repository 类型
            for alias in node.names:
                if "Repository" in alias.name:
                    violations.append(
                        f"line {node.lineno}: from {module} import {alias.name}",
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "sqlalchemy.ext.asyncio" in alias.name:
                    violations.append(
                        f"line {node.lineno}: import {alias.name}",
                    )

    return violations


# ── Router 架构边界测试 ──────────────────────────────────────────────────


@pytest.mark.g1
@pytest.mark.unit
def test_router_files_exist() -> None:
    """确认测试扫描到了 Router 文件.

    如果此测试失败，说明 Router 文件查找逻辑有误，
    或项目中不存在任何 Router 文件。
    """

    files = _find_router_files()
    assert len(files) > 0, "未找到任何 Router 文件"


@pytest.mark.g1
@pytest.mark.unit
def test_router_does_not_import_async_session_or_repository() -> None:
    """Router 模块不导入 AsyncSession 或 Repository（SPEC 5.6 / 34.1）.

    通过 AST 静态扫描 Router 源文件，验证:
      - 不导入 ``sqlalchemy.ext.asyncio``（AsyncSession）
      - 不导入任何 Repository 类型

    SPEC 5.6: "Router 只能获得 Use Case，不得获得 AsyncSession、
    Repository 或提交接口"。
    """

    files = _find_router_files()
    all_violations: dict[str, list[str]] = {}

    for file_path in files:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
        violations = _get_forbidden_imports(tree)
        if violations:
            rel_path = file_path.relative_to(_PROJECT_ROOT)
            all_violations[str(rel_path)] = violations

    assert not all_violations, (
        "以下 Router 文件包含禁止的导入（AsyncSession 或 Repository）:\n"
        + "\n".join(
            f"  {path}:\n" + "\n".join(f"    {v}" for v in violations)
            for path, violations in all_violations.items()
        )
    )
