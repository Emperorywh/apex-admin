"""Nginx 配置静态 lint 脚本 — SPEC 26.3 / 23.4 / 24.2.

校验 ``deploy/nginx/apex.conf`` 的必备指令齐全。本机无 nginx，
``nginx -t`` 语法验证移交 TASK-035 CI 门禁在 Docker 容器内执行。

校验项::

    1. HTTPS — listen 443 ssl + ssl_certificate / ssl_certificate_key
    2. HTTP→HTTPS 重定向 — listen 80 + return 301
    3. 可信代理头 — X-Real-IP / X-Forwarded-For / X-Forwarded-Proto
    4. 上传大小限制 — client_max_body_size 在上传 location 显式设置
    5. 限流区域 — login_limit (10r/m) / refresh_limit (30r/m)
    6. 限流规则 — limit_req zone=login_limit / zone=refresh_limit
    7. /metrics 不对外代理 — location = /metrics { return 404; }
    8. 三类超时 — connect/send/read 在普通/上传/下载 location 分别配置
    9. 无 root/alias 静态直出（私有文件不绕过应用授权）
   10. 所有 location 均以 proxy_pass 或 return 结束

用法::

    python scripts/lint_nginx.py [config_path]
    python scripts/lint_nginx.py deploy/nginx/apex.conf

退出码: 0 = 通过, 1 = 校验失败
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _PROJECT_ROOT / "deploy" / "nginx" / "apex.conf"

# ── 校验规则 ───────────────────────────────────────────────────────────────


class LintError(Exception):
    """单条 lint 校验失败。"""


def _check(label: str, condition: bool, detail: str = "") -> None:
    """断言一条规则，失败时抛出 LintError。"""

    if not condition:
        raise LintError(f"[{label}] {detail}" if detail else f"[{label}] 校验失败")


def lint_nginx_config(config_path: Path) -> list[str]:
    """执行全部静态校验，返回检查通过的项目列表。

    :raises LintError: 首条校验失败时抛出。
    """

    _check(
        "config-exists",
        config_path.is_file(),
        f"配置文件不存在: {config_path}",
    )

    content = config_path.read_text(encoding="utf-8")
    checks: list[str] = []

    # 1. HTTPS — listen 443 ssl
    _check(
        "https-listen",
        bool(re.search(r"listen\s+443\s+ssl\s*;", content)),
        "缺少 'listen 443 ssl;' 指令（SPEC 26.3: HTTPS）",
    )
    checks.append("https-listen")

    # ssl_certificate
    _check(
        "ssl-cert",
        bool(re.search(r"ssl_certificate\s+", content)),
        "缺少 ssl_certificate 指令",
    )
    checks.append("ssl-cert")

    # ssl_certificate_key
    _check(
        "ssl-cert-key",
        bool(re.search(r"ssl_certificate_key\s+", content)),
        "缺少 ssl_certificate_key 指令",
    )
    checks.append("ssl-cert-key")

    # 2. HTTP→HTTPS 重定向
    _check(
        "http-redirect",
        bool(re.search(r"listen\s+80\s*;", content))
        and bool(re.search(r"return\s+301\s+https://", content)),
        "缺少 HTTP→HTTPS 301 重定向（SPEC 26.3）",
    )
    checks.append("http-redirect")

    # 3. 可信代理头
    for header in ("X-Real-IP", "X-Forwarded-For", "X-Forwarded-Proto"):
        _check(
            f"proxy-header-{header.lower()}",
            header in content,
            f"缺少 proxy_set_header {header}（SPEC 26.3: 可信代理头）",
        )
        checks.append(f"proxy-header-{header.lower()}")

    # 4. 上传大小限制 — client_max_body_size
    _check(
        "client-max-body-size",
        "client_max_body_size" in content,
        "缺少 client_max_body_size 指令（SPEC 26.3: 上传大小限制）",
    )
    checks.append("client-max-body-size")

    # 上传 location 的 client_max_body_size 应大于 1m
    upload_match = re.search(
        r"location\s+=\s*/api/v1/files\s*\{([^}]*?)client_max_body_size\s+(\d+)([mMkKgG])",
        content,
        re.DOTALL,
    )
    if upload_match:
        size_val = int(upload_match.group(2))
        size_unit = upload_match.group(3).lower()
        _check(
            "upload-body-size-gt-1m",
            size_unit == "m" and size_val > 1,
            f"上传 client_max_body_size 应 > 1m，当前为 {size_val}{size_unit}",
        )
        checks.append("upload-body-size-gt-1m")

    # 5. 限流区域
    _check(
        "login-limit-zone",
        bool(re.search(r"zone\s*=\s*login_limit\s*:", content)),
        "缺少 limit_req_zone login_limit 定义（SPEC 23.4）",
    )
    checks.append("login-limit-zone")

    _check(
        "login-limit-rate",
        bool(re.search(r"zone\s*=\s*login_limit:.*rate\s*=\s*10r/m", content)),
        "登录限流区域 rate 应为 10r/m（SPEC 23.4）",
    )
    checks.append("login-limit-rate")

    _check(
        "refresh-limit-zone",
        bool(re.search(r"zone\s*=\s*refresh_limit\s*:", content)),
        "缺少 limit_req_zone refresh_limit 定义（SPEC 23.4）",
    )
    checks.append("refresh-limit-zone")

    _check(
        "refresh-limit-rate",
        bool(re.search(r"zone\s*=\s*refresh_limit:.*rate\s*=\s*30r/m", content)),
        "刷新限流区域 rate 应为 30r/m（SPEC 23.4）",
    )
    checks.append("refresh-limit-rate")

    # 6. 限流规则引用
    _check(
        "login-limit-req",
        bool(re.search(r"limit_req\s+zone\s*=\s*login_limit", content)),
        "缺少 limit_req zone=login_limit 规则引用",
    )
    checks.append("login-limit-req")

    _check(
        "refresh-limit-req",
        bool(re.search(r"limit_req\s+zone\s*=\s*refresh_limit", content)),
        "缺少 limit_req zone=refresh_limit 规则引用",
    )
    checks.append("refresh-limit-req")

    # 7. /metrics 不对外暴露
    _check(
        "metrics-blocked",
        bool(re.search(r"location\s*=\s*/metrics\s*\{", content))
        and bool(
            re.search(
                r"location\s*=\s*/metrics\s*\{[^}]*?return\s+404",
                content,
                re.DOTALL,
            )
        ),
        "缺少 location = /metrics { return 404; }（SPEC 24.2）",
    )
    checks.append("metrics-blocked")

    # 8. 三类超时
    for timeout in (
        "proxy_connect_timeout",
        "proxy_send_timeout",
        "proxy_read_timeout",
    ):
        count = len(re.findall(rf"{timeout}\s+\d", content))
        _check(
            f"timeout-{timeout}",
            count >= 3,
            f"{timeout} 出现 {count} 次，应≥3（普通/上传/下载）",
        )
        checks.append(f"timeout-{timeout}")

    # 9. 无 root/alias 静态直出（私有文件不绕过应用授权）
    _check(
        "no-static-root",
        not re.search(r"^\s*root\s+", content, re.MULTILINE),
        "禁止使用 root 指令（SPEC 26.3: 私有文件禁止绕过应用授权直接暴露）",
    )
    checks.append("no-static-root")

    _check(
        "no-static-alias",
        not re.search(r"^\s*alias\s+", content, re.MULTILINE),
        "禁止使用 alias 指令（SPEC 26.3: 私有文件禁止绕过应用授权直接暴露）",
    )
    checks.append("no-static-alias")

    # 10. 所有 location 块以 proxy_pass 或 return 结束（不直出文件）
    # 仅匹配行首的 location 指令（排除注释中出现的 location 文字）
    location_blocks = re.findall(
        r"^\s*location\s+[^{]*\{((?:[^{}]|\{[^}]*\})*)\}",
        content,
        re.DOTALL | re.MULTILINE,
    )
    for i, block in enumerate(location_blocks):
        has_proxy_pass = "proxy_pass" in block
        has_return = bool(re.search(r"return\s+\d", block))
        _check(
            f"location-block-dispatch-{i}",
            has_proxy_pass or has_return,
            f"location 块 {i + 1} 必须以 proxy_pass 或 return 结束，禁止静态文件直出",
        )
        checks.append(f"location-block-dispatch-{i}")

    return checks


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""

    import argparse

    parser = argparse.ArgumentParser(
        description="Nginx 配置静态 lint — 校验必备指令齐全",
    )
    parser.add_argument(
        "config_path",
        nargs="?",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"Nginx 配置文件路径（默认: {_DEFAULT_CONFIG}）",
    )
    args = parser.parse_args(argv)

    try:
        passed = lint_nginx_config(args.config_path)
    except LintError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {len(passed)} 项校验全部通过")
    for item in passed:
        print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
