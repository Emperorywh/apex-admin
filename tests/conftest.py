"""pytest 全局配置 — 测试数据库供应链.

三级供应链（SPEC 28.2，顺序固定）::

    1. 显式环境变量 ``APEX_TEST_DATABASE_URL``
    2. Docker 可用时使用 Testcontainers PostgreSQL 18（CI 默认路径）
    3. 本地已供应 PostgreSQL 二进制临时实例（本地无 Docker 时回退）

Tier 3 复用 ``scripts/dev_db.py`` 下载的 EDB 免安装二进制，在 pytest 会话内
创建独立的临时数据目录与临时端口，会话结束后自动停止并清理。
三级皆不可用时以明确指引失败。

禁止使用 SQLite 替代 PostgreSQL（SPEC 5.4 / 28.2）。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

# ─── 将 scripts/ 加入模块搜索路径以复用 dev_db 的二进制管理逻辑 ────────────

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import dev_db  # noqa: E402,I001


# ─── 辅助函数 ───────────────────────────────────────────────────────────────


def _docker_available() -> bool:
    """检查 Docker 守护进程是否可用。

    Testcontainers 依赖 Docker 运行时。如果 Docker 未安装或守护进程
    未启动，返回 False 以触发 Tier 3 回退。
    """

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _find_free_port() -> int:
    """获取一个操作系统分配的可用临时端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _cleanup_temp_dir(data_dir: Path) -> None:
    """安全清理临时数据目录，带重试以应对 Windows 文件锁。

    ``pg_ctl stop`` 后文件句柄可能需要短暂时间释放。本函数在删除
    失败时等待并重试，避免 ``shutil.rmtree(ignore_errors=True)``
    静默吞没清理失败导致残留目录。
    """

    # 短暂等待文件句柄释放（stop_server 内部已做进程级清理）。
    time.sleep(0.5)

    for attempt in range(3):
        if not data_dir.exists():
            return
        try:
            shutil.rmtree(data_dir)
        except OSError:
            if attempt < 2:
                time.sleep(1.0)
            else:
                print(
                    f"[conftest] 警告：临时数据目录清理失败：{data_dir}",
                    file=sys.stderr,
                )


# ─── 会话级 fixture：测试数据库连接 URL ────────────────────────────────────


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """提供测试数据库连接 URL（会话级）。

    供应链顺序：
      1. 显式 ``APEX_TEST_DATABASE_URL`` 环境变量
      2. Docker 可用时使用 Testcontainers PostgreSQL 18
      3. 本地已供应二进制临时实例（自动启停、自动清理）

    三级皆不可用时通过 ``pytest.fail`` 给出明确指引。
    返回的 URL 格式为 ``postgresql+psycopg://``（SPEC 8.1）。
    """

    # ── Tier 1：显式环境变量 ──
    explicit_url = os.environ.get("APEX_TEST_DATABASE_URL")
    if explicit_url:
        yield explicit_url
        return

    # ── Tier 2：Testcontainers（CI 默认路径）──
    if _docker_available():
        try:
            from testcontainers.postgres import PostgresContainer

            container = PostgresContainer("postgres:18")
            container.start()
            # testcontainers 默认返回 postgresql+psycopg2://，
            # 替换为项目使用的 postgresql+psycopg:// 驱动。
            url = container.get_connection_url()
            url = url.replace(
                "postgresql+psycopg2://",
                "postgresql+psycopg://",
                1,
            )
            yield url
            container.stop()
            return
        except Exception as exc:
            # Testcontainers 启动失败不中断，回退到 Tier 3。
            print(
                f"[conftest] Testcontainers 启动失败，回退到本地二进制：{exc}",
                file=sys.stderr,
            )

    # ── Tier 3：本地已供应二进制临时实例 ──
    if not dev_db.is_provisioned():
        pytest.fail(
            "无法获取测试数据库，三级供应链均不可用：\n"
            "  1. 环境变量 APEX_TEST_DATABASE_URL 未设置\n"
            "  2. Docker 不可用（Testcontainers 需要 Docker）\n"
            "  3. 本地 PostgreSQL 二进制未供应\n"
            "\n"
            "解决方法（任选其一）：\n"
            "  - 设置 APEX_TEST_DATABASE_URL 指向已有 PostgreSQL 18 实例\n"
            "  - 启动 Docker Desktop 后重新运行测试\n"
            "  - 运行 'uv run python scripts/dev_db.py ensure' 供应本地二进制",
            pytrace=False,
        )

    # 使用临时数据目录和临时端口，与开发实例完全隔离。
    port = _find_free_port()
    data_dir = Path(tempfile.mkdtemp(prefix="apex_test_pg_"))
    log_file = data_dir / "pg.log"
    pg_bin_dir = dev_db.get_pg_bin_dir()

    try:
        dev_db.run_initdb(pg_bin_dir, data_dir)
        # 临时实例为短生命周期，禁用 autovacuum 避免其 worker 在
        # Windows 上崩溃（0xC0000142）触发 crash-recovery，
        # 导致 pg_ctl stop 无法正常终止。
        dev_db.start_server(
            pg_bin_dir,
            data_dir,
            port,
            log_file,
            extra_opts="-c autovacuum=off",
        )
        url = dev_db.get_connection_string(
            port=port,
            database=dev_db.PG_DATABASE,
            username=dev_db.PG_SUPERUSER,
        )
        yield url
    finally:
        # 确保 PostgreSQL 进程停止和临时目录清理位于同一 finally 路径。
        dev_db.stop_server(pg_bin_dir, data_dir)
        _cleanup_temp_dir(data_dir)
