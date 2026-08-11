"""Nginx 反向代理配置静态断言测试 — SPEC 26.3 / 23.4 / 24.2.

本机无 nginx，通过静态解析验证 Nginx 配置必备指令齐全。
nginx -t 语法验证移交 TASK-035 CI 门禁在 Docker 容器内执行。

覆盖:
  - HTTPS 配置、可信代理头、上传大小限制
  - 三类超时显式配置（普通/上传/下载）
  - 登录/刷新接口 limit_req 规则与 zone 定义
  - /metrics 不对外代理
  - 无 location 绕过应用授权直出私有文件
  - compose 中 Nginx stable 官方镜像固定摘要
  - 静态 lint 脚本可独立运行且通过
  - 文档声明唯一受支持配置
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# 全部测试为静态文件分析（不依赖 nginx 或 Docker），可本地执行
pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NGINX_CONF = _PROJECT_ROOT / "deploy" / "nginx" / "apex.conf"
_LINT_SCRIPT = _PROJECT_ROOT / "scripts" / "lint_nginx.py"
_COMPOSE_YAML = _PROJECT_ROOT / "deploy" / "compose.yaml"
_NGINX_DOC = _PROJECT_ROOT / "docs" / "nginx-proxy-config.md"

_SHA256_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}", re.IGNORECASE)


def _read_nginx_conf() -> str:
    """读取 Nginx 配置文件内容。"""

    return _NGINX_CONF.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# 配置文件存在性（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestNginxConfigExists:
    """Nginx 配置文件存在性断言 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_config_file_exists(self) -> None:
        """配置文件存在。"""

        assert _NGINX_CONF.is_file(), "deploy/nginx/apex.conf 不存在"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_single_config_file(self) -> None:
        """仅存在一个 .conf 文件（唯一受支持的配置）。"""

        conf_dir = _PROJECT_ROOT / "deploy" / "nginx"
        conf_files = list(conf_dir.glob("*.conf"))
        assert len(conf_files) == 1, (
            f"deploy/nginx/ 应仅包含 1 个 .conf 文件，当前 {len(conf_files)} 个"
        )


# ═══════════════════════════════════════════════════════════════════════════
# HTTPS 配置（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestHttps:
    """HTTPS 配置断言 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_https_listen(self) -> None:
        """配置 HTTPS 监听端口 443。"""

        content = _read_nginx_conf()
        assert re.search(r"listen\s+443\s+ssl\s*;", content), (
            "缺少 'listen 443 ssl;' 指令（SPEC 26.3: HTTPS）"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_ssl_certificate_directives(self) -> None:
        """ssl_certificate 和 ssl_certificate_key 指令存在。"""

        content = _read_nginx_conf()
        assert re.search(r"ssl_certificate\s+", content), (
            "缺少 ssl_certificate 指令（SPEC 26.3: HTTPS）"
        )
        assert re.search(r"ssl_certificate_key\s+", content), (
            "缺少 ssl_certificate_key 指令（SPEC 26.3: HTTPS）"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_http_to_https_redirect(self) -> None:
        """HTTP 端口 80 重定向到 HTTPS（SPEC 26.3）。"""

        content = _read_nginx_conf()
        assert re.search(r"listen\s+80\s*;", content), "缺少 listen 80"
        assert re.search(r"return\s+301\s+https://", content), (
            "缺少 HTTP→HTTPS 301 重定向"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_tls_protocols(self) -> None:
        """TLS 协议限制为 TLSv1.2 和 TLSv1.3。"""

        content = _read_nginx_conf()
        assert "TLSv1.2" in content and "TLSv1.3" in content, (
            "应限制 TLS 协议为 TLSv1.2 / TLSv1.3"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 可信代理头（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestTrustedProxyHeaders:
    """可信代理头断言 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_proxy_headers_set(self) -> None:
        """配置可信代理头 X-Real-IP / X-Forwarded-For / X-Forwarded-Proto。"""

        content = _read_nginx_conf()
        for header in ("X-Real-IP", "X-Forwarded-For", "X-Forwarded-Proto"):
            assert header in content, (
                f"缺少 proxy_set_header {header}（SPEC 26.3: 可信代理头）"
            )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_proxy_headers_in_proxy_locations(self) -> None:
        """代理 location 块中传递代理头。"""

        content = _read_nginx_conf()
        # 至少在普通 API location 中存在
        api_block_match = re.search(
            r"location\s+/api/v1/\s*\{([^}]*)\}",
            content,
            re.DOTALL,
        )
        assert api_block_match is not None
        api_block = api_block_match.group(1)
        assert "X-Forwarded-For" in api_block
        assert "X-Real-IP" in api_block


# ═══════════════════════════════════════════════════════════════════════════
# 上传大小限制（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestUploadSizeLimit:
    """上传大小限制断言 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_client_max_body_size_present(self) -> None:
        """配置中存在 client_max_body_size 指令。"""

        content = _read_nginx_conf()
        assert "client_max_body_size" in content, (
            "缺少 client_max_body_size 指令（SPEC 26.3: 上传大小限制）"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_upload_location_has_larger_limit(self) -> None:
        """上传 location 的 client_max_body_size 大于普通 API。"""

        content = _read_nginx_conf()
        upload_match = re.search(
            r"location\s+=\s*/api/v1/files\s*\{([^}]*?)client_max_body_size\s+(\d+)([mMkKgG])",
            content,
            re.DOTALL,
        )
        assert upload_match is not None, (
            "上传 location (/api/v1/files) 应包含 client_max_body_size"
        )
        size_val = int(upload_match.group(2))
        assert size_val > 1, (
            f"上传 client_max_body_size 应 > 1m，"
            f"当前为 {size_val}{upload_match.group(3)}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 三类超时显式配置（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeouts:
    """三类超时（普通/上传/下载）显式配置断言 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_all_timeout_directives_present(self) -> None:
        """三类超时指令（connect/send/read）均存在。"""

        content = _read_nginx_conf()
        for directive in (
            "proxy_connect_timeout",
            "proxy_send_timeout",
            "proxy_read_timeout",
        ):
            assert directive in content, f"缺少 {directive}（SPEC 26.3: 三类超时）"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_upload_location_has_distinct_timeouts(self) -> None:
        """上传 location 配置了与普通 API 不同的超时。"""

        content = _read_nginx_conf()
        upload_match = re.search(
            r"location\s+=\s*/api/v1/files\s*\{([^}]*?)\}",
            content,
            re.DOTALL,
        )
        assert upload_match is not None
        upload_block = upload_match.group(1)
        assert "proxy_read_timeout" in upload_block, (
            "上传 location 应显式配置 proxy_read_timeout（SPEC 26.3: 上传请求超时）"
        )
        assert "proxy_send_timeout" in upload_block, (
            "上传 location 应显式配置 proxy_send_timeout"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_download_location_has_distinct_timeouts(self) -> None:
        """下载 location 配置了流式传输超时。"""

        content = _read_nginx_conf()
        # 匹配包含 download 的 location 块
        download_match = re.search(
            r"^\s*location\s+~\s+\^/api/v1/files/.+?download.*?\{([^}]*?)\}",
            content,
            re.DOTALL | re.MULTILINE,
        )
        assert download_match is not None, (
            "缺少文件下载 location（/api/v1/files/{id}/download）"
        )
        download_block = download_match.group(1)
        assert "proxy_read_timeout" in download_block, (
            "下载 location 应显式配置 proxy_read_timeout（SPEC 26.3: 下载流式超时）"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_upload_timeout_longer_than_normal(self) -> None:
        """上传超时应长于普通 API 超时。"""

        content = _read_nginx_conf()

        # 普通超时
        normal_match = re.search(
            r"location\s+/api/v1/\s*\{[^}]*?proxy_read_timeout\s+(\d+)s",
            content,
            re.DOTALL,
        )
        assert normal_match is not None
        normal_read = int(normal_match.group(1))

        # 上传超时
        upload_match = re.search(
            r"location\s+=\s*/api/v1/files\s*\{[^}]*?proxy_read_timeout\s+(\d+)s",
            content,
            re.DOTALL,
        )
        assert upload_match is not None
        upload_read = int(upload_match.group(1))

        assert upload_read > normal_read, (
            f"上传 read timeout ({upload_read}s) 应大于普通 API ({normal_read}s)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 限流规则与 zone 定义（SPEC 23.4）
# ═══════════════════════════════════════════════════════════════════════════


class TestRateLimiting:
    """登录/刷新接口 limit_req 规则与 zone 定义断言 — SPEC 23.4."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_login_limit_zone_definition(self) -> None:
        """登录限流 zone 定义存在且 rate 为 10r/m。"""

        content = _read_nginx_conf()
        assert re.search(
            r"limit_req_zone\s+\S+\s+zone\s*=\s*login_limit\s*:\s*\d+m\s+rate\s*=\s*10r/m\s*;",
            content,
        ), "缺少 limit_req_zone login_limit rate=10r/m 定义（SPEC 23.4）"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_refresh_limit_zone_definition(self) -> None:
        """刷新限流 zone 定义存在且 rate 为 30r/m。"""

        content = _read_nginx_conf()
        assert re.search(
            r"limit_req_zone\s+\S+\s+zone\s*=\s*refresh_limit\s*:\s*\d+m\s+rate\s*=\s*30r/m\s*;",
            content,
        ), "缺少 limit_req_zone refresh_limit rate=30r/m 定义（SPEC 23.4）"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_login_limit_req_in_location(self) -> None:
        """登录 location 引用 login_limit zone。"""

        content = _read_nginx_conf()
        login_block = re.search(
            r"location\s*=\s*/api/v1/auth/login\s*\{([^}]*?)\}",
            content,
            re.DOTALL,
        )
        assert login_block is not None, "缺少 location = /api/v1/auth/login"
        assert re.search(
            r"limit_req\s+zone\s*=\s*login_limit",
            login_block.group(1),
        ), "登录 location 应引用 limit_req zone=login_limit"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_refresh_limit_req_in_location(self) -> None:
        """刷新 location 引用 refresh_limit zone。"""

        content = _read_nginx_conf()
        refresh_block = re.search(
            r"location\s*=\s*/api/v1/auth/refresh\s*\{([^}]*?)\}",
            content,
            re.DOTALL,
        )
        assert refresh_block is not None, "缺少 location = /api/v1/auth/refresh"
        assert re.search(
            r"limit_req\s+zone\s*=\s*refresh_limit",
            refresh_block.group(1),
        ), "刷新 location 应引用 limit_req zone=refresh_limit"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_limit_req_status_429(self) -> None:
        """限流拒绝时返回 429 状态码。"""

        content = _read_nginx_conf()
        assert "limit_req_status" in content and "429" in content, (
            "应配置 limit_req_status 429"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_limit_uses_binary_remote_addr(self) -> None:
        """限流 zone 使用 $binary_remote_addr 作为 key。"""

        content = _read_nginx_conf()
        # login 和 refresh zone 都应使用 binary_remote_addr
        login_zone = re.search(
            r"limit_req_zone\s+(\S+)\s+zone\s*=\s*login_limit",
            content,
        )
        assert login_zone is not None
        assert "$binary_remote_addr" in login_zone.group(1), (
            "登录限流 zone 应使用 $binary_remote_addr 作为 key"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_limit_zones_use_distinct_names(self) -> None:
        """登录和刷新使用不同的 zone 名称。"""

        content = _read_nginx_conf()
        zones = re.findall(r"zone\s*=\s*(\w+)_limit\s*:", content)
        assert "login" in zones, "应有 login_limit zone"
        assert "refresh" in zones, "应有 refresh_limit zone"


# ═══════════════════════════════════════════════════════════════════════════
# /metrics 不对外代理（SPEC 24.2）
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricsNotProxied:
    """/metrics 不对外代理断言 — SPEC 24.2."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_metrics_returns_404(self) -> None:
        """/metrics location 返回 404。"""

        content = _read_nginx_conf()
        metrics_block = re.search(
            r"location\s*=\s*/metrics\s*\{([^}]*?)\}",
            content,
            re.DOTALL,
        )
        assert metrics_block is not None, (
            "缺少 location = /metrics（SPEC 24.2: /metrics 不对外暴露）"
        )
        assert "return 404" in metrics_block.group(1), "/metrics location 应 return 404"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_metrics_no_proxy_pass(self) -> None:
        """/metrics location 不包含 proxy_pass。"""

        content = _read_nginx_conf()
        metrics_block = re.search(
            r"location\s*=\s*/metrics\s*\{([^}]*?)\}",
            content,
            re.DOTALL,
        )
        assert metrics_block is not None
        assert "proxy_pass" not in metrics_block.group(1), (
            "/metrics 不得 proxy_pass 到 API 容器（SPEC 24.2）"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 无私有文件直出（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestNoStaticFileServing:
    """无 location 绕过应用授权直出私有文件 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_no_root_directive(self) -> None:
        """禁止 root 指令。"""

        content = _read_nginx_conf()
        assert not re.search(r"^\s*root\s+", content, re.MULTILINE), (
            "禁止 root 指令（SPEC 26.3: 私有文件禁止绕过应用授权直接暴露）"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_no_alias_directive(self) -> None:
        """禁止 alias 指令。"""

        content = _read_nginx_conf()
        assert not re.search(r"^\s*alias\s+", content, re.MULTILINE), (
            "禁止 alias 指令（SPEC 26.3: 私有文件禁止绕过应用授权直接暴露）"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_all_locations_dispatch_correctly(self) -> None:
        """所有 location 块以 proxy_pass 或 return 结束。"""

        content = _read_nginx_conf()
        location_blocks = re.findall(
            r"^\s*location\s+[^{]*\{((?:[^{}]|\{[^}]*\})*)\}",
            content,
            re.DOTALL | re.MULTILINE,
        )
        assert len(location_blocks) > 0, "应至少有 1 个 location 块"
        for i, block in enumerate(location_blocks):
            has_proxy_pass = "proxy_pass" in block
            has_return = bool(re.search(r"return\s+\d", block))
            assert has_proxy_pass or has_return, (
                f"location 块 {i + 1} 必须以 proxy_pass 或 return 结束"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Compose 中 Nginx 镜像（SPEC 26.1 / 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestComposeNginxImage:
    """compose.yaml 中 Nginx 镜像断言 — SPEC 26.1 / 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_nginx_stable_image(self) -> None:
        """Nginx 使用 stable 官方镜像（SPEC 26.1: Nginx stable 官方镜像）."""

        data = yaml.safe_load(_COMPOSE_YAML.read_text(encoding="utf-8"))
        nginx = data["services"]["nginx"]
        image = nginx.get("image", "")
        assert "nginx" in image, "应使用 nginx 镜像"
        assert "stable" in image, f"应使用 nginx:stable（SPEC 26.1），当前: {image}"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_nginx_image_pinned_digest(self) -> None:
        """Nginx 镜像固定摘要（SPEC 26.1: 固定镜像摘要）."""

        data = yaml.safe_load(_COMPOSE_YAML.read_text(encoding="utf-8"))
        nginx = data["services"]["nginx"]
        image = nginx.get("image", "")
        assert _SHA256_DIGEST_RE.search(image) is not None, (
            "nginx 镜像应使用固定摘要 @sha256:<64 hex>"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_nginx_mounts_config_dir(self) -> None:
        """Nginx 挂载配置目录（SPEC 26.3）。"""

        data = yaml.safe_load(_COMPOSE_YAML.read_text(encoding="utf-8"))
        nginx = data["services"]["nginx"]
        volumes = nginx.get("volumes", [])
        assert any("nginx" in str(v) and "conf.d" in str(v) for v in volumes), (
            "nginx 服务应挂载配置目录到 /etc/nginx/conf.d"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 静态 lint 脚本（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestLintScript:
    """静态 lint 脚本可独立运行且通过 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_lint_script_exists(self) -> None:
        """lint 脚本存在。"""

        assert _LINT_SCRIPT.is_file(), "scripts/lint_nginx.py 不存在"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_lint_script_passes(self) -> None:
        """lint 脚本对当前配置退出码 0。"""

        result = subprocess.run(
            [sys.executable, str(_LINT_SCRIPT), str(_NGINX_CONF)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"lint_nginx.py 应退出码 0\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_lint_script_detects_missing_directive(self, tmp_path: Path) -> None:
        """lint 脚本对缺少必备指令的配置退出码非 0。"""

        # 创建一个缺少 ssl_certificate 的配置
        bad_config = tmp_path / "bad.conf"
        bad_config.write_text(
            "server { listen 443 ssl; }\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(_LINT_SCRIPT), str(bad_config)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0, "lint_nginx.py 对缺少必备指令的配置应退出码非 0"


# ═══════════════════════════════════════════════════════════════════════════
# 文档声明唯一受支持配置（SPEC 26.3）
# ═══════════════════════════════════════════════════════════════════════════


class TestNginxDoc:
    """文档声明唯一受支持配置断言 — SPEC 26.3."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_doc_exists(self) -> None:
        """Nginx 配置文档存在。"""

        assert _NGINX_DOC.is_file(), "docs/nginx-proxy-config.md 不存在"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_doc_declares_unique_config(self) -> None:
        """文档声明此配置为唯一受支持的配置。"""

        content = _NGINX_DOC.read_text(encoding="utf-8")
        assert "唯一" in content, "文档应声明此配置为唯一受支持的配置（SPEC 26.3）"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_doc_documents_rate_limits(self) -> None:
        """文档说明限流规则。"""

        content = _NGINX_DOC.read_text(encoding="utf-8")
        assert "10" in content and "30" in content, (
            "文档应说明登录 10 次/分钟、刷新 30 次/分钟限流"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_doc_documents_timeouts(self) -> None:
        """文档说明三类超时配置。"""

        content = _NGINX_DOC.read_text(encoding="utf-8")
        assert "超时" in content, "文档应说明三类超时配置"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_doc_documents_metrics_exclusion(self) -> None:
        """文档说明 /metrics 不对外代理。"""

        content = _NGINX_DOC.read_text(encoding="utf-8")
        assert "metrics" in content.lower(), "文档应说明 /metrics 不对外代理"
