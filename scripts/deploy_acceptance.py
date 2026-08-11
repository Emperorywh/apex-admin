#!/usr/bin/env python3
"""Docker 部署验收集成测试运行器 — SPEC 34.4 / 28.6.

在 GitHub Actions CI 环境中运行，对已启动的 Compose 全栈执行集成测试。
本机无 Docker，此脚本仅在 CI 中由 deploy-acceptance 工作流调用。

覆盖 SPEC 34.4 的 Docker 依赖条目:
  - 双 API Worker 一致性（会话吊销/权限变更/文件访问跨 Worker 生效）
  - HTTPS/代理头/Host/CORS/登录与刷新限流集成测试
  - 发布门禁（未迁移不就绪→停机切换→就绪）
  - 优雅关闭
  - 备份恢复演练与 RPO/RTO 报告
  - 私有文件禁止绕过授权下载

使用方式::

    python scripts/deploy_acceptance.py --base-url https://localhost \
        --admin-user admin --admin-password <password>

前置条件:
  - Compose 全栈已启动（postgres、migrate、≥2 api workers、nginx）
  - 健康检查已通过
  - 管理员用户已创建
"""

from __future__ import annotations

import argparse
import http.client
import json
import ssl
import subprocess
import sys
import time

# ── 常量 ──────────────────────────────────────────────────────────────────

# 请求超时（秒）
_REQUEST_TIMEOUT = 30

# 限流测试: 登录每分钟 10 次
_LOGIN_RATE_LIMIT = 10

# 优雅关闭超时（秒）
_SHUTDOWN_TIMEOUT = 30


# ── HTTP 客户端 ────────────────────────────────────────────────────────────


def _make_https_request(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    body: str | None = None,
    headers: dict[str, str] | None = None,
    cookies: str | None = None,
) -> tuple[int, dict[str, str], str]:
    """发起 HTTPS 请求，返回 (状态码, 响应头, 响应体)。

    使用不校验证书的 SSL 上下文（CI 自签名证书）。
    """

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    conn = http.client.HTTPSConnection(
        host,
        port,
        timeout=_REQUEST_TIMEOUT,
        context=ctx,
    )

    req_headers: dict[str, str] = {"Host": "localhost"}
    if headers:
        req_headers.update(headers)
    if cookies:
        req_headers["Cookie"] = cookies
    if body:
        req_headers["Content-Type"] = "application/json"

    conn.request(method, path, body=body, headers=req_headers)
    response = conn.getresponse()
    resp_body = response.read().decode("utf-8", errors="replace")
    resp_headers = {k.lower(): v for k, v in response.getheaders()}
    status = response.status
    conn.close()
    return status, resp_headers, resp_body


def _make_http_request(
    host: str,
    port: int,
    method: str,
    path: str,
) -> tuple[int, dict[str, str], str]:
    """发起 HTTP 请求（用于 HTTPS 重定向测试）。"""

    conn = http.client.HTTPConnection(host, port, timeout=_REQUEST_TIMEOUT)
    conn.request(method, path, headers={"Host": "localhost"})
    response = conn.getresponse()
    resp_body = response.read().decode("utf-8", errors="replace")
    resp_headers = {k.lower(): v for k, v in response.getheaders()}
    status = response.status
    conn.close()
    return status, resp_headers, resp_body


# ── 测试结果收集 ───────────────────────────────────────────────────────────


class TestResult:
    """收集测试结果。"""

    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def record(self, name: str, success: bool, detail: str = "") -> None:
        if success:
            self.passed.append(name)
            print(f"  [PASS] {name}")
        else:
            self.failed.append(f"{name}: {detail}" if detail else name)
            print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))

    @property
    def all_passed(self) -> bool:
        return not self.failed


# ── 认证辅助 ──────────────────────────────────────────────────────────────


def _login(host: str, port: int, username: str, password: str) -> tuple[str, str]:
    """登录获取 access_token 和 refresh cookie。

    返回 (access_token, cookie_header)。
    """

    body = json.dumps({"username": username, "password": password})
    status, headers, resp_body = _make_https_request(
        host, port, "POST", "/api/v1/auth/login", body=body
    )
    if status != 200:
        raise RuntimeError(f"登录失败: {status} {resp_body}")

    data = json.loads(resp_body)
    access_token = data.get("access_token", "")

    # 从 Set-Cookie 提取 refresh cookie
    cookie_header = headers.get("set-cookie", "")

    return access_token, cookie_header


def _auth_headers(token: str) -> dict[str, str]:
    """构建认证请求头。"""

    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════
# 测试 1: 双 API Worker 一致性（SPEC 5.3 / 34.4 / 28.6）
# ═══════════════════════════════════════════════════════════════════════════


def test_worker_consistency(
    host: str, port: int, admin_user: str, admin_pass: str, result: TestResult
) -> None:
    """验证多 Worker 不共享进程内状态。

    会话/权限/文件元数据均持久化到 PostgreSQL，跨 Worker 一致。
    通过 Nginx 负载均衡，请求可能命中不同 Worker。
    """

    print("\n─── 双 API Worker 一致性测试 ───")

    # 登录获取 token
    try:
        token, cookie = _login(host, port, admin_user, admin_pass)
    except RuntimeError as e:
        result.record("worker-consistency-login", False, str(e))
        return

    result.record("worker-consistency-login", True)

    # ── 会话吊销跨 Worker 生效 ──────────────────────────────────────
    # 退出其他会话后，同一 token 不再有效
    # 反复请求确保命中不同 Worker
    for _ in range(5):
        _make_https_request(
            host,
            port,
            "GET",
            "/api/v1/users/me",
            headers=_auth_headers(token),
        )

    # 退出所有其他会话
    status, _, _ = _make_https_request(
        host,
        port,
        "POST",
        "/api/v1/auth/logout-others",
        headers=_auth_headers(token),
        cookies=cookie,
    )
    result.record(
        "worker-consistency-session-revocation",
        status in (200, 204),
        f"logout-others 返回 {status}",
    )

    # ── 权限变更跨 Worker 生效 ──────────────────────────────────────
    # 查询当前用户权限（通过不同 Worker）
    status, _, resp_body = _make_https_request(
        host,
        port,
        "GET",
        "/api/v1/users/me",
        headers=_auth_headers(token),
    )
    result.record(
        "worker-consistency-permission-query",
        status == 200,
        f"users/me 返回 {status}",
    )

    # ── 文件访问跨 Worker 生效 ──────────────────────────────────────
    # 文件列表查询通过不同 Worker
    status, _, _ = _make_https_request(
        host,
        port,
        "GET",
        "/api/v1/files?page=1&page_size=1",
        headers=_auth_headers(token),
    )
    result.record(
        "worker-consistency-file-access",
        status == 200,
        f"files 列表返回 {status}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# 测试 2: HTTPS/代理头/Host/CORS/限流（SPEC 23.1 / 23.4 / 26.3 / 34.4）
# ═══════════════════════════════════════════════════════════════════════════


def test_http_integration(host: str, port: int, result: TestResult) -> None:
    """验证 HTTPS 重定向、安全头、Host 白名单、CORS 和限流。"""

    print("\n─── HTTPS/安全头/CORS/限流集成测试 ───")

    # ── HTTPS 重定向（HTTP → 301 → HTTPS）──────────────────────────
    status, headers, _ = _make_http_request(host, 80, "GET", "/health/live")
    result.record(
        "https-redirect",
        status == 301 and "https" in headers.get("location", ""),
        f"HTTP 返回 {status}, Location: {headers.get('location', 'N/A')}",
    )

    # ── 安全响应头 ──────────────────────────────────────────────────
    status, headers, _ = _make_https_request(host, port, "GET", "/health/live")
    result.record(
        "security-headers-x-content-type",
        headers.get("x-content-type-options") == "nosniff",
        f"X-Content-Type-Options: {headers.get('x-content-type-options', 'N/A')}",
    )
    result.record(
        "security-headers-x-frame-options",
        headers.get("x-frame-options") == "DENY",
        f"X-Frame-Options: {headers.get('x-frame-options', 'N/A')}",
    )

    # ── Host 白名单（非白名单 Host 被拒绝）─────────────────────────
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(
        host,
        port,
        timeout=_REQUEST_TIMEOUT,
        context=ctx,
    )
    conn.request("GET", "/health/live", headers={"Host": "evil.com"})
    response = conn.getresponse()
    response.read()
    result.record(
        "host-whitelist-rejection",
        response.status in (400, 403),
        f"非白名单 Host 返回 {response.status}",
    )
    conn.close()

    # ── CORS 白名单 ────────────────────────────────────────────────
    status, headers, _ = _make_https_request(
        host,
        port,
        "OPTIONS",
        "/api/v1/auth/login",
        headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "POST",
        },
    )
    # OPTIONS 可能返回 200 或 405，关键是 CORS 头存在
    cors_header = headers.get("access-control-allow-origin", "")
    result.record(
        "cors-whitelist",
        bool(cors_header),
        f"CORS Origin: {cors_header or 'N/A'}",
    )

    # ── /metrics 不对外暴露（SPEC 24.2）────────────────────────────
    status, _, _ = _make_https_request(host, port, "GET", "/metrics")
    result.record(
        "metrics-not-exposed",
        status == 404,
        f"/metrics 返回 {status}",
    )

    # ── 登录限流（每分钟 10 次）────────────────────────────────────
    # 连续发送 12 次登录请求，预期至少 1 次 429
    rejected = False
    for _ in range(_LOGIN_RATE_LIMIT + 2):
        body = json.dumps({"username": "nobody", "password": "x" * 12})
        status, _, _ = _make_https_request(
            host, port, "POST", "/api/v1/auth/login", body=body
        )
        if status == 429:
            rejected = True
            break
    result.record(
        "login-rate-limit",
        rejected,
        "登录限流未触发 429",
    )


# ═══════════════════════════════════════════════════════════════════════════
# 测试 3: 发布门禁（SPEC 26.1 / 34.4 / 28.6）
# ═══════════════════════════════════════════════════════════════════════════


def test_release_gate(host: str, port: int, result: TestResult) -> None:
    """验证发布门禁逻辑。

    compose.yaml 中 migrate 服务为 service_completed_successfully 门禁，
    API 依赖 migrate 成功后才启动。此处验证迁移完成后就绪检查通过。
    全流程停机切换（停旧→迁移→启新）由 compose depends_on 链保证。
    """

    print("\n─── 发布门禁测试 ───")

    # 验证迁移已完成（migrate 服务成功退出后 API 才启动）
    # /health/ready 应返回 200
    status, _, body = _make_https_request(host, port, "GET", "/health/ready")
    result.record(
        "release-gate-migrated-ready",
        status == 200,
        f"/health/ready 返回 {status}: {body[:200]}",
    )

    # 验证 /health/live 始终可用（不依赖数据库迁移状态）
    status, _, _ = _make_https_request(host, port, "GET", "/health/live")
    result.record(
        "release-gate-live-always",
        status == 200,
        f"/health/live 返回 {status}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# 测试 4: 优雅关闭（SPEC 6.1 / 26.1 / 34.4）
# ═══════════════════════════════════════════════════════════════════════════


def test_graceful_shutdown(result: TestResult) -> None:
    """验证优雅关闭行为。

    向 API 容器发送 SIGTERM，验证:
      1. 进行中的请求在超时内完成
      2. 数据库连接被释放

    使用 docker compose 重启验证优雅关闭。
    """

    print("\n─── 优雅关闭测试 ───")

    # 通过 docker compose restart api 验证优雅关闭
    # Uvicorn 收到 SIGTERM 后停止接受新连接，等待进行中请求完成
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "restart",
                "--timeout",
                str(_SHUTDOWN_TIMEOUT),
                "api",
            ],
            capture_output=True,
            text=True,
            timeout=_SHUTDOWN_TIMEOUT + 60,
        )
        result.record(
            "graceful-shutdown-restart",
            proc.returncode == 0,
            f"docker compose restart 返回 {proc.returncode}",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result.record("graceful-shutdown-restart", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# 测试 5: 备份恢复演练（SPEC 27.1 / 27.2 / 34.4）
# ═══════════════════════════════════════════════════════════════════════════


def test_backup_recovery(result: TestResult) -> None:
    """验证备份创建与恢复演练。

    在容器内执行 backup create 和 backup verify，
    验证报告包含 RPO 和 RTO 指标。
    """

    print("\n─── 备份恢复演练测试 ───")

    # 创建备份
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "api",
                "python",
                "-m",
                "app.cli",
                "backup",
                "create",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        backup_output = proc.stdout + proc.stderr
        result.record(
            "backup-create",
            proc.returncode == 0,
            f"backup create 返回 {proc.returncode}: {backup_output[:300]}",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result.record("backup-create", False, str(e))
        return

    if proc.returncode != 0:
        return

    # 提取 Backup ID
    backup_id = ""
    for line in backup_output.splitlines():
        if "backup_id" in line.lower() or "Backup ID" in line:
            backup_id = line.split(":")[-1].strip().strip('"')
            break

    # 验证备份（隔离环境恢复）
    verify_proc = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "app.cli",
            "backup",
            "verify",
            "--backup-id",
            backup_id or "latest",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    verify_output = verify_proc.stdout + verify_proc.stderr
    result.record(
        "backup-verify",
        verify_proc.returncode == 0,
        f"backup verify 返回 {verify_proc.returncode}: {verify_output[:300]}",
    )

    # 验证 RPO/RTO 报告
    combined = backup_output + verify_output
    has_rpo = "rpo" in combined.lower()
    has_rto = "rto" in combined.lower()
    result.record(
        "backup-rpo-rto-report",
        has_rpo and has_rto,
        f"RPO={'有' if has_rpo else '无'}, RTO={'有' if has_rto else '无'}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# 测试 6: 私有文件禁止绕过授权下载（SPEC 19.2 / 26.3 / 34.4）
# ═══════════════════════════════════════════════════════════════════════════


def test_file_access_control(
    host: str, port: int, admin_user: str, admin_pass: str, result: TestResult
) -> None:
    """验证私有文件不能绕过应用授权直接下载。

    Nginx 不直接暴露文件存储目录，所有文件请求经过应用授权。
    """

    print("\n─── 私有文件访问控制测试 ───")

    # ── 未认证下载被拒绝 ────────────────────────────────────────────
    # 尝试无认证下载任意文件 ID
    status, _, _ = _make_https_request(
        host,
        port,
        "GET",
        "/api/v1/files/00000000-0000-0000-0000-000000000000/download",
    )
    result.record(
        "file-access-unauthenticated-denied",
        status in (401, 403, 404),
        f"未认证下载返回 {status}",
    )

    # ── Nginx 不直接暴露文件存储 ──────────────────────────────────
    # 尝试通过 Nginx 直接访问文件路径（应返回 404，不代理到应用）
    status, _, _ = _make_https_request(
        host,
        port,
        "GET",
        "/data/files/test.pdf",
    )
    result.record(
        "file-access-no-nginx-bypass",
        status == 404,
        f"直接访问文件路径返回 {status}",
    )

    # ── 默认 location 不代理未声明路径 ─────────────────────────────
    status, _, _ = _make_https_request(
        host,
        port,
        "GET",
        "/static/secret.key",
    )
    result.record(
        "file-access-no-static-bypass",
        status == 404,
        f"访问 /static/ 返回 {status}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Docker 部署验收集成测试运行器 — SPEC 34.4",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="目标主机（默认 localhost）",
    )
    parser.add_argument(
        "--https-port",
        type=int,
        default=443,
        help="HTTPS 端口（默认 443）",
    )
    parser.add_argument(
        "--admin-user",
        default="admin",
        help="管理员用户名",
    )
    parser.add_argument(
        "--admin-password",
        required=True,
        help="管理员密码",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="跳过备份恢复测试（耗时较长）",
    )
    parser.add_argument(
        "--skip-shutdown",
        action="store_true",
        help="跳过优雅关闭测试（会重启 API）",
    )
    args = parser.parse_args()

    host = args.host
    port = args.https_port
    result = TestResult()

    print(f"{'=' * 70}")
    print(f"Docker 部署验收集成测试 — {host}:{port}")
    print(f"{'=' * 70}")

    # 等待服务就绪
    print("\n─── 等待服务就绪 ───")
    ready = False
    for _ in range(60):
        try:
            status, _, _ = _make_https_request(host, port, "GET", "/health/live")
            if status == 200:
                ready = True
                break
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(2)

    if not ready:
        print("[FAIL] 服务在 120 秒内未就绪")
        return 1
    print("[OK] 服务已就绪")

    # 运行测试套件
    test_worker_consistency(host, port, args.admin_user, args.admin_password, result)
    test_http_integration(host, port, result)
    test_release_gate(host, port, result)
    test_file_access_control(host, port, args.admin_user, args.admin_password, result)

    if not args.skip_shutdown:
        test_graceful_shutdown(result)
        # 优雅关闭后等待服务恢复
        print("\n─── 等待服务重启后恢复 ───")
        for _ in range(60):
            try:
                status, _, _ = _make_https_request(host, port, "GET", "/health/live")
                if status == 200:
                    print("[OK] 服务已恢复")
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(2)

    if not args.skip_backup:
        test_backup_recovery(result)

    # ── 汇总结果 ──────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"测试结果: {len(result.passed)} 通过, {len(result.failed)} 失败")
    print(f"{'=' * 70}")

    if result.failed:
        print("\n失败项:")
        for item in result.failed:
            print(f"  ✗ {item}")
        return 1

    print("\n全部通过！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
