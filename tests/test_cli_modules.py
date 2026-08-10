"""CLI modules validate 命令测试 — SPEC 25.1.

覆盖验收标准:
  - AC-4: ``uv run python -m app.cli modules validate`` 在核心装配下
    退出码 0 且报告零重复，并同时校验 Alembic 单 head。
  - AC-0: 冲突场景下退出码非 0。

SPEC 25.1: ``uv run python -m app.cli modules validate`` 验证模块编码、
路由、权限点、错误码、事件和 Alembic 单 head。
"""

from __future__ import annotations

import pytest

from app.cli.__main__ import main as cli_main


@pytest.mark.g1
@pytest.mark.unit
def test_modules_validate_exits_zero_on_clean_manifest() -> None:
    """核心装配下 modules validate 退出码 0 且报告零重复（AC-4）。

    G1 阶段模块清单为空，校验应通过。
    """

    exit_code = cli_main(["modules", "validate"])
    assert exit_code == 0


@pytest.mark.g1
@pytest.mark.unit
def test_modules_validate_reports_zero_duplicates() -> None:
    """modules validate 输出包含零重复声明报告（AC-4）。"""

    # 捕获 stdout 验证输出内容
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = cli_main(["modules", "validate"])

    assert exit_code == 0
    output = buf.getvalue()
    assert "零重复" in output
    assert "模块校验通过" in output


@pytest.mark.g1
@pytest.mark.unit
def test_modules_validate_reports_alembic_head() -> None:
    """modules validate 输出包含 Alembic 单 head 校验结果（AC-4）。"""

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli_main(["modules", "validate"])

    output = buf.getvalue()
    assert "Alembic" in output


@pytest.mark.g1
@pytest.mark.unit
def test_modules_validate_with_duplicate_detection() -> None:
    """冲突场景下 modules validate 退出码非 0（AC-0）。

    使用 monkeypatch 替换模块清单，构造重复声明场景。
    """

    import io
    from contextlib import redirect_stderr, redirect_stdout
    from unittest.mock import patch

    from app.core.modules.definition import ModuleDefinition

    dup_module_a = ModuleDefinition(
        code="dup_a",
        api_tag="shared_tag",
        permission_codes=("system:test:read",),
    )
    dup_module_b = ModuleDefinition(
        code="dup_b",
        api_tag="shared_tag",  # 重复 tag
        permission_codes=("system:test:read",),  # 重复权限
    )

    with patch(
        "app.composition.modules.get_module_manifest",
        return_value=[dup_module_a, dup_module_b],
    ):
        buf_err = io.StringIO()
        buf_out = io.StringIO()
        with redirect_stderr(buf_err), redirect_stdout(buf_out):
            exit_code = cli_main(["modules", "validate"])

    assert exit_code != 0
    assert "模块校验失败" in buf_err.getvalue()
