#!/usr/bin/env python3
"""本地 PostgreSQL 18 免安装供应脚本.

在无 Docker/WSL 的 Windows 开发环境中，下载 EDB 官方 PostgreSQL 18.x 免安装
二进制至 ``.runtime/``，执行 ``initdb`` 初始化数据目录，并通过 ``pg_ctl`` 管理
服务的启动、停止和状态查询。``ensure`` 命令幂等地组合下载、初始化与启动，
重复执行不会重建已有数据目录。

安全约束：
    - ``initdb`` 禁止以管理员权限运行（PostgreSQL 安全限制）。
    - 服务仅监听 ``127.0.0.1``，认证模式为 ``trust``，仅限本地回环。
    - 端口避开默认 ``5432``，默认使用 ``55432``。

使用方式::

    uv run python scripts/dev_db.py ensure   # 下载 + 初始化 + 启动（幂等）
    uv run python scripts/dev_db.py status   # 查看运行状态与连接串
    uv run python scripts/dev_db.py start    # 仅启动
    uv run python scripts/dev_db.py stop     # 停止服务

环境变量：
    APEX_PG_DOWNLOAD_URL: 覆盖默认下载地址（用于升级到新版 18.x 二进制）。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# ─── 常量 ───────────────────────────────────────────────────────────────────

# PostgreSQL 主版本号 — SPEC 5.4 固定 PostgreSQL 18.x
PG_MAJOR_VERSION: int = 18

# EDB 官方 PostgreSQL 18.4 Windows x64 免安装二进制下载地址。
# 更新版本时，从以下页面获取最新 18.x Windows x64 Binaries 的直链：
#   https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
# 更新后同步修改 PG_VERSION_STRING。
DOWNLOAD_URL: str = os.environ.get(
    "APEX_PG_DOWNLOAD_URL",
    "https://sbp.enterprisedb.com/getfile.jsp?fileid=1260303",
)

# 下载文件的预期版本字符串（用于解压后版本校验）。
PG_VERSION_STRING: str = "18.4"

# 下载 ZIP 的预期 SHA-256 摘要；留空则跳过摘要校验，仅依赖解压后
# ``postgres --version`` 进行版本校验。
DOWNLOAD_SHA256: str = ""

# 本地运行时根目录（已在 .gitignore 中排除，二进制与数据目录不会进入版本控制）。
RUNTIME_DIR: Path = Path(".runtime")

# 解压后的 PostgreSQL 主目录（含 bin/、lib/、share/ 等）。
PG_HOME: Path = RUNTIME_DIR / "pg18"

# 开发实例数据目录。
PG_DATA_DIR: Path = RUNTIME_DIR / "pgdata"

# 服务日志文件。
PG_LOG_FILE: Path = RUNTIME_DIR / "pg.log"

# 下载的 ZIP 暂存路径。
PG_ZIP_FILE: Path = RUNTIME_DIR / "pg18.zip"

# 开发实例端口 — 避开默认 5432，防止与系统 PostgreSQL 冲突。
DEFAULT_PORT: int = 55432

# 数据库超级用户名。
PG_SUPERUSER: str = "apex"

# 默认数据库名（initdb 总是创建 postgres 数据库）。
PG_DATABASE: str = "postgres"


# ─── 路径辅助 ──────────────────────────────────────────────────────────────


def get_pg_bin_dir() -> Path:
    """返回 PostgreSQL ``bin/`` 目录路径。"""

    return PG_HOME / "bin"


def get_pg_executable(name: str) -> str:
    """返回指定 PostgreSQL 可执行文件的完整路径。

    在 Windows 上自动添加 ``.exe`` 后缀。
    """

    exe = f"{name}.exe" if sys.platform == "win32" else name
    return str(get_pg_bin_dir() / exe)


def is_provisioned() -> bool:
    """检查 PostgreSQL 二进制是否已下载并解压完成。"""

    return Path(get_pg_executable("initdb")).exists()


def is_initialized(data_dir: Path | None = None) -> bool:
    """检查数据目录是否已完成 ``initdb``。"""

    d = data_dir or PG_DATA_DIR
    return (d / "PG_VERSION").exists()


def is_running(data_dir: Path | None = None) -> bool:
    """通过 ``pg_ctl status`` 检查服务是否正在运行。"""

    d = data_dir or PG_DATA_DIR
    if not is_initialized(d):
        return False
    result = subprocess.run(
        [get_pg_executable("pg_ctl"), "status", "-D", str(d)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ─── 下载与解压 ─────────────────────────────────────────────────────────────


def _check_not_admin() -> None:
    """Windows 下检查是否以管理员权限运行。

    ``initdb`` 禁止以管理员权限执行（PostgreSQL 安全限制），
    在 Windows 上需要提前拦截。
    """

    if sys.platform != "win32":
        return
    try:
        import ctypes

        if ctypes.windll.shell32.IsUserAnAdmin():  # type: ignore[attr-defined]
            print(
                "错误：initdb 禁止以管理员权限运行。请使用普通用户终端执行本脚本。",
                file=sys.stderr,
            )
            raise SystemExit(1)
    except (AttributeError, OSError):
        # 无法判断权限时不阻塞流程，initdb 自身会拒绝管理员执行。
        pass


def download_binary() -> None:
    """下载 PostgreSQL 免安装二进制 ZIP 至 ``.runtime/``。

    优先使用 ``curl``（带断点续传与重试）以应对 EDB CDN 的连接波动；
    系统无 ``curl`` 时回退到 ``urllib`` 流式下载。
    下载完成后校验文件大小并计算 SHA-256 摘要。
    如果 ZIP 文件已存在且大小完整则跳过下载。
    """

    if PG_ZIP_FILE.exists() and _is_zip_valid(PG_ZIP_FILE):
        print(f"下载文件已存在且完整，跳过下载：{PG_ZIP_FILE}")
        return

    PG_ZIP_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"正在下载 PostgreSQL {PG_VERSION_STRING} 二进制...")
    print(f"下载地址：{DOWNLOAD_URL}")

    # 优先使用 curl：支持断点续传、自动重试和 SSL 兼容性。
    curl_path = shutil.which("curl")
    if curl_path:
        _download_with_curl(curl_path)
    else:
        _download_with_urllib()

    # 下载后校验完整性。
    if not _is_zip_valid(PG_ZIP_FILE):
        print("错误：下载文件不完整或损坏（ZIP 校验失败）。", file=sys.stderr)
        raise SystemExit(1)

    actual_sha256 = _compute_sha256(PG_ZIP_FILE)
    print(f"SHA-256：{actual_sha256}")

    if DOWNLOAD_SHA256 and actual_sha256 != DOWNLOAD_SHA256:
        PG_ZIP_FILE.unlink(missing_ok=True)
        print(
            f"错误：SHA-256 校验失败。期望 {DOWNLOAD_SHA256}，实际 {actual_sha256}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _is_zip_valid(path: Path) -> bool:
    """检查文件是否为有效的 ZIP 归档。"""

    try:
        with zipfile.ZipFile(path, "r") as zf:
            # 触发中央目录读取，无效 ZIP 会抛出异常。
            zf.namelist()
        return True
    except (zipfile.BadZipFile, OSError):
        return False


def _compute_sha256(path: Path) -> str:
    """计算文件的 SHA-256 摘要。"""

    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def _download_with_curl(curl_path: str) -> None:
    """使用 curl 下载（支持断点续传、自动重试）。"""

    result = subprocess.run(
        [
            curl_path,
            "--location",  # 跟随重定向
            "--output",
            str(PG_ZIP_FILE),
            "--continue-at",
            "-",  # 断点续传
            "--retry",
            "5",  # 失败自动重试 5 次
            "--retry-delay",
            "5",  # 重试间隔 5 秒
            "--fail",  # HTTP 错误返回非零退出码
            "--progress-bar",  # 显示进度条
            DOWNLOAD_URL,
        ],
    )
    if result.returncode != 0:
        PG_ZIP_FILE.unlink(missing_ok=True)
        print(f"错误：curl 下载失败（退出码 {result.returncode}）", file=sys.stderr)
        raise SystemExit(1)


def _download_with_urllib() -> None:
    """使用 urllib 流式下载（无 curl 时的回退路径）。"""

    sha256 = hashlib.sha256()
    try:
        with (
            urllib.request.urlopen(DOWNLOAD_URL) as resp,
            open(PG_ZIP_FILE, "wb") as f,
        ):
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            # 1 MB 每块，兼顾下载效率与进度刷新频率。
            chunk_size = 1024 * 1024
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                sha256.update(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded * 100 // total
                    print(
                        f"\r  {downloaded // (1024 * 1024)} MB / "
                        f"{total // (1024 * 1024)} MB ({pct}%)",
                        end="",
                        flush=True,
                    )
    except urllib.error.URLError as exc:
        PG_ZIP_FILE.unlink(missing_ok=True)
        print(f"\n错误：下载失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print()


def _find_pg_root(extract_dir: Path) -> Path | None:
    """在解压目录中查找包含 ``bin/initdb`` 的根目录。

    EDB 的 Windows ZIP 可能直接解压出 ``bin/``，也可能嵌套一层版本目录。
    本函数兼容两种结构。
    """

    exe = "initdb.exe" if sys.platform == "win32" else "initdb"

    # 情况一：解压根目录直接包含 bin/initdb。
    if (extract_dir / "bin" / exe).exists():
        return extract_dir

    # 情况二：解压出一个子目录，子目录内包含 bin/initdb。
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "bin" / exe).exists():
            return child

    return None


def extract_binary() -> None:
    """解压 PostgreSQL 二进制 ZIP 至 ``.runtime/pg18/``。

    解压后自动校验 ``postgres --version`` 输出的主版本号。
    """

    if is_provisioned():
        print(f"二进制已解压，跳过：{PG_HOME}")
        return

    if not PG_ZIP_FILE.exists():
        print("错误：下载文件不存在，请先执行 download。", file=sys.stderr)
        raise SystemExit(1)

    PG_HOME.parent.mkdir(parents=True, exist_ok=True)
    print(f"正在解压至 {PG_HOME} ...")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(PG_ZIP_FILE, "r") as zf:
            zf.extractall(tmp_path)

        pg_root = _find_pg_root(tmp_path)
        if pg_root is None:
            print(
                "错误：ZIP 中未找到 PostgreSQL bin 目录（缺少 initdb）。",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # 将找到的根目录移动到 PG_HOME。
        if pg_root == tmp_path:
            PG_HOME.mkdir(parents=True, exist_ok=True)
            for item in tmp_path.iterdir():
                shutil.move(str(item), str(PG_HOME / item.name))
        else:
            shutil.move(str(pg_root), str(PG_HOME))

    # 版本校验：确认解压后的二进制主版本符合预期。
    version = verify_version()
    print(f"已安装 PostgreSQL 版本：{version}")

    if not version.startswith(f"{PG_MAJOR_VERSION}."):
        print(
            f"错误：期望 PostgreSQL {PG_MAJOR_VERSION}.x，实际 {version}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def verify_version() -> str:
    """运行 ``postgres --version`` 并返回纯版本号字符串。

    返回值格式如 ``18.4``。如果二进制不存在或版本无法解析则终止程序。
    """

    result = subprocess.run(
        [get_pg_executable("postgres"), "--version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            f"错误：无法获取 PostgreSQL 版本：{result.stderr}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # 输出格式：'postgres (PostgreSQL) 18.4'
    parts = result.stdout.strip().split()
    return parts[-1] if parts else ""


# ─── 服务管理 ───────────────────────────────────────────────────────────────


def run_initdb(
    pg_bin_dir: Path,
    data_dir: Path,
    username: str = PG_SUPERUSER,
) -> None:
    """对指定数据目录执行 ``initdb``。

    参数:
        pg_bin_dir: PostgreSQL ``bin/`` 目录路径。
        data_dir: 待初始化的数据目录路径（必须不存在或为空）。
        username: 数据库超级用户名。
    """

    _check_not_admin()

    data_dir.parent.mkdir(parents=True, exist_ok=True)
    initdb = str(pg_bin_dir / ("initdb.exe" if sys.platform == "win32" else "initdb"))

    print(f"正在初始化数据目录：{data_dir}")
    result = subprocess.run(
        [
            initdb,
            "-D",
            str(data_dir),
            "-U",
            username,
            "-A",
            "trust",
            "--encoding=UTF8",
            "--locale=C",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"错误：initdb 失败：\n{result.stderr}", file=sys.stderr)
        raise SystemExit(1)


def start_server(
    pg_bin_dir: Path,
    data_dir: Path,
    port: int,
    log_file: Path,
    extra_opts: str = "",
) -> None:
    """通过 ``pg_ctl start`` 启动 PostgreSQL 服务。

    服务仅监听 ``127.0.0.1``，认证模式为 trust。

    参数:
        pg_bin_dir: PostgreSQL ``bin/`` 目录路径。
        data_dir: 已初始化的数据目录路径。
        port: 监听端口。
        log_file: 服务日志输出文件路径。
        extra_opts: 追加到 ``-o`` 的额外 PostgreSQL 配置选项
            （如 ``-c autovacuum=off``），用于临时实例的定制化启动。
    """

    pg_ctl = str(
        pg_bin_dir / ("pg_ctl.exe" if sys.platform == "win32" else "pg_ctl"),
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"正在启动 PostgreSQL（端口 {port}）...")
    # 构建 PostgreSQL 启动配置选项。
    options = f"-c port={port} -c listen_addresses=127.0.0.1"
    if extra_opts:
        options += f" {extra_opts}"

    # 注意：不能使用 capture_output=True（即 stdout/stderr=PIPE）。
    # 在 Windows 上，pg_ctl start 会启动后台 postgres.exe 进程，
    # 该进程继承 PIPE 句柄并保持打开，导致 subprocess.run 永久阻塞。
    # 使用 DEVNULL 避免句柄继承；服务输出已由 -l 重定向到日志文件。
    result = subprocess.run(
        [
            pg_ctl,
            "start",
            "-D",
            str(data_dir),
            "-l",
            str(log_file),
            "-w",
            "-t",
            "30",
            "-o",
            options,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        # 从日志文件获取错误详情。
        log_tail = ""
        if log_file.exists():
            log_tail = log_file.read_text(encoding="utf-8", errors="replace")[-500:]
        print(
            f"错误：启动失败（退出码 {result.returncode}）：\n{log_tail}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"PostgreSQL 已启动（端口 {port}）")


def _read_postmaster_pid(data_dir: Path) -> int | None:
    """读取数据目录中 ``postmaster.pid`` 文件的首行 PID。

    返回 None 表示文件不存在或无法解析（服务已停止或路径无效）。
    """

    pid_file = data_dir / "postmaster.pid"
    if not pid_file.exists():
        return None
    try:
        first_line = pid_file.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()[0]
        return int(first_line.strip())
    except (ValueError, IndexError, OSError):
        return None


def _is_process_running(pid: int) -> bool:
    """检查指定 PID 的进程是否仍在运行（Windows 使用 tasklist）。"""

    if pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    # CSV 格式下，存在匹配进程时输出以双引号开头
    # （如 "postgres.exe","1234",...）；无匹配时输出信息行，不以引号开头。
    stdout = result.stdout.strip()
    return bool(stdout) and stdout.startswith('"')


def _ensure_postmaster_stopped(data_dir: Path) -> None:
    """Windows 防御：验证 postmaster 进程已退出，必要时强制终止。

    pg_ctl stop 可能因 crash-recovery 状态而无法终止全部子进程
    （如 autovacuum worker 崩溃后 postmaster 进入恢复模式）。
    本函数读取 postmaster.pid 获取 PID，轮询验证进程退出，
    超时后使用 taskkill /F /T 强制终止进程树。
    """

    pid = _read_postmaster_pid(data_dir)
    if pid is None:
        return

    # 轮询验证进程已退出（最多 5 秒）。
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _is_process_running(pid):
            return
        time.sleep(0.5)

    # 进程仍在运行，强制终止进程树。
    print(
        f"  postmaster (PID {pid}) 未正常退出，强制终止进程树...",
        file=sys.stderr,
    )
    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )

    # 等待文件句柄释放。
    time.sleep(0.5)


def stop_server(pg_bin_dir: Path, data_dir: Path) -> None:
    """通过 ``pg_ctl stop`` 停止 PostgreSQL 服务（fast 模式）。

    在 Windows 上，``pg_ctl stop`` 可能因 crash-recovery 状态而无法
    终止全部子进程。本函数在 ``pg_ctl stop -w`` 完成后验证 postmaster
    进程是否已退出，必要时强制终止进程树以确保资源释放。

    参数:
        pg_bin_dir: PostgreSQL ``bin/`` 目录路径。
        data_dir: 数据目录路径。
    """

    pg_ctl = str(
        pg_bin_dir / ("pg_ctl.exe" if sys.platform == "win32" else "pg_ctl"),
    )
    print("正在停止 PostgreSQL...")
    # 同 start_server，不使用 capture_output 避免 Windows 管道句柄继承。
    # -w 显式等待停止完成（pg_ctl stop 默认不等待）。
    result = subprocess.run(
        [pg_ctl, "stop", "-D", str(data_dir), "-w", "-m", "fast", "-t", "60"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        # 服务可能已经停止，输出警告但不终止程序。
        print(
            f"警告：pg_ctl stop 未成功（退出码 {result.returncode}）",
            file=sys.stderr,
        )

    # Windows 防御：即使 pg_ctl 报告成功，也验证 postmaster 进程已退出。
    # 临时实例可能因子进程崩溃触发 crash-recovery，导致 pg_ctl stop
    # 返回成功但 postmaster 子进程仍在运行。
    if sys.platform == "win32":
        _ensure_postmaster_stopped(data_dir)

    print("PostgreSQL 已停止")


def get_connection_string(
    port: int = DEFAULT_PORT,
    database: str = PG_DATABASE,
    username: str = PG_SUPERUSER,
) -> str:
    """返回 SQLAlchemy 兼容的连接串。

    格式为 ``postgresql+psycopg://<user>@127.0.0.1:<port>/<database>``，
    与 SPEC 8.1 保持一致（psycopg 驱动）。
    """

    return f"postgresql+psycopg://{username}@127.0.0.1:{port}/{database}"


# ─── CLI 命令 ───────────────────────────────────────────────────────────────


def cmd_ensure() -> int:
    """幂等地完成下载、初始化与启动。

    重复执行时：二进制已下载则跳过下载，数据目录已存在则跳过 initdb，
    服务已运行则跳过启动。
    """

    # 步骤 1：下载与解压二进制。
    if not is_provisioned():
        download_binary()
        extract_binary()
    else:
        print(f"PostgreSQL 二进制已就绪：{PG_HOME}")

    # 步骤 2：初始化数据目录（幂等：已存在则不重建）。
    if not is_initialized():
        run_initdb(get_pg_bin_dir(), PG_DATA_DIR)
    else:
        print(f"数据目录已存在，跳过 initdb：{PG_DATA_DIR}")

    # 步骤 3：启动服务（幂等：已运行则跳过）。
    if not is_running():
        start_server(get_pg_bin_dir(), PG_DATA_DIR, DEFAULT_PORT, PG_LOG_FILE)
    else:
        print("PostgreSQL 服务已在运行")

    # 输出状态摘要。
    _print_status()
    return 0


def cmd_start() -> int:
    """仅启动服务（不执行下载和初始化）。"""

    if not is_provisioned():
        print("错误：二进制未下载，请先执行 ensure。", file=sys.stderr)
        return 1
    if not is_initialized():
        print("错误：数据目录未初始化，请先执行 ensure。", file=sys.stderr)
        return 1
    if is_running():
        print("PostgreSQL 服务已在运行")
        _print_status()
        return 0

    start_server(get_pg_bin_dir(), PG_DATA_DIR, DEFAULT_PORT, PG_LOG_FILE)
    _print_status()
    return 0


def cmd_stop() -> int:
    """停止服务。"""

    if not is_initialized():
        print("数据目录不存在，无需停止。")
        return 0
    if not is_running():
        print("PostgreSQL 服务未运行。")
        return 0

    stop_server(get_pg_bin_dir(), PG_DATA_DIR)
    return 0


def cmd_status() -> int:
    """查询并输出服务状态、版本与连接串。"""

    _print_status()
    return 0


def _print_status() -> None:
    """打印当前供应状态摘要。"""

    print()
    print("═══ PostgreSQL 本地供应状态 ═══")
    print(f"  二进制目录：{PG_HOME if is_provisioned() else '未下载'}")
    print(f"  数据目录：  {PG_DATA_DIR if is_initialized() else '未初始化'}")
    print(f"  运行状态：  {'运行中' if is_running() else '已停止'}")

    if is_provisioned():
        try:
            version = verify_version()
            print(f"  版本：      {version}")
        except SystemExit:
            print("  版本：      无法获取")
    else:
        print(f"  期望版本：  {PG_VERSION_STRING}")

    if is_running():
        conn_str = get_connection_string()
        print(f"  连接串：    {conn_str}")
    print("═════════════════════════════════")


# ─── 入口 ───────────────────────────────────────────────────────────────────


def main() -> int:
    """解析命令行参数并分发到对应子命令。"""

    parser = argparse.ArgumentParser(
        description="本地 PostgreSQL 18 免安装供应工具。",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure", help="下载 + 初始化 + 启动（幂等）")
    sub.add_parser("start", help="仅启动服务")
    sub.add_parser("stop", help="停止服务")
    sub.add_parser("status", help="查看运行状态与连接串")

    args = parser.parse_args()

    if args.command == "ensure":
        return cmd_ensure()
    elif args.command == "start":
        return cmd_start()
    elif args.command == "stop":
        return cmd_stop()
    elif args.command == "status":
        return cmd_status()
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
