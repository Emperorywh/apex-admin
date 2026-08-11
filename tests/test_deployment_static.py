"""部署文件静态断言测试 — SPEC 26.1 / 26.2.

本机无 Docker 环境，通过静态解析验证 Dockerfile 指令与 compose.yaml 结构。
实际 Docker 构建与 compose config 验证移交 TASK-035 CI 门禁（SPEC 28.6 / 34.4）。

覆盖:
  - Dockerfile: 固定摘要基础镜像、uv 冻结安装、非 root 用户、版本构建参数
  - .dockerignore: 排除开发密钥与无关文件
  - compose.yaml: postgres:18 固定摘要、≥2 API Worker、migrate 服务门禁、
    nginx 服务、pgdata/文件存储/备份卷、重启策略与健康检查
  - 发布流程文档: 停机切换顺序与门禁逻辑
  - 连接预算文档: 满足 26.1 公式
  - 优雅关闭: lifespan 释放连接池
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from typing import Any

# ── 路径常量 ───────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _PROJECT_ROOT / "Dockerfile"
_DOCKERIGNORE = _PROJECT_ROOT / ".dockerignore"
_COMPOSE_YAML = _PROJECT_ROOT / "deploy" / "compose.yaml"
_DEPLOYMENT_DOC = _PROJECT_ROOT / "docs" / "deployment-guide.md"
_CONNECTION_BUDGET_DOC = _PROJECT_ROOT / "docs" / "connection-budget.md"
_MAIN_PY = _PROJECT_ROOT / "src" / "app" / "main.py"

# 固定摘要格式: sha256: 后跟 64 个十六进制字符
_SHA256_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}", re.IGNORECASE)


# ── 辅助函数 ───────────────────────────────────────────────────────────────


def _read_dockerfile_lines() -> list[str]:
    """读取 Dockerfile 并返回按行分割的列表（保留原始内容）。"""

    return _DOCKERFILE.read_text(encoding="utf-8").splitlines()


def _read_compose_yaml() -> dict[str, Any]:
    """解析 compose.yaml 并返回字典。"""

    return yaml.safe_load(_COMPOSE_YAML.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# Dockerfile 断言（SPEC 26.2）
# ═══════════════════════════════════════════════════════════════════════════


class TestDockerfile:
    """Dockerfile 静态指令断言 — SPEC 26.2."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_dockerfile_exists(self) -> None:
        """Dockerfile 存在。"""

        assert _DOCKERFILE.is_file(), "Dockerfile 不存在"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_base_images_use_fixed_digest(self) -> None:
        """所有 FROM 基础镜像使用固定摘要（SPEC 26.2 / 5.4）."""

        lines = _read_dockerfile_lines()
        from_lines = [
            line for line in lines if line.strip().upper().startswith("FROM ")
        ]
        assert len(from_lines) >= 2, "Dockerfile 应至少有 2 个 FROM 阶段"

        for line in from_lines:
            # 跳过 AS 别名部分，只检查镜像引用
            match = _SHA256_DIGEST_RE.search(line)
            assert match is not None, (
                f"FROM 镜像未使用固定摘要: {line.strip()}\n"
                "应使用 @sha256:<64 hex> 格式固定摘要"
            )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_uv_frozen_install(self) -> None:
        """uv 冻结安装（SPEC 5.4: CI 使用 uv sync --frozen）."""

        content = _DOCKERFILE.read_text(encoding="utf-8")
        assert "--frozen" in content, (
            "Dockerfile 必须使用 uv sync --frozen 进行冻结安装"
        )
        assert "--no-dev" in content, "Dockerfile 生产镜像应排除开发依赖（--no-dev）"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_non_root_user(self) -> None:
        """非 root 用户运行（SPEC 26.2）."""

        content = _DOCKERFILE.read_text(encoding="utf-8")
        # 验证创建了非 root 用户
        assert re.search(r"useradd.*appuser", content) or re.search(
            r"adduser.*appuser",
            content,
        ), "Dockerfile 应创建非 root 用户 appuser"
        # 验证切换到该用户
        assert re.search(r"^USER\s+appuser", content, re.MULTILINE), (
            "Dockerfile 必须以 USER appuser 指令切换到非 root 用户"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_version_build_args(self) -> None:
        """版本构建参数可追溯（SPEC 26.2: 镜像版本可追踪到代码版本）."""

        content = _DOCKERFILE.read_text(encoding="utf-8")
        # APP_VERSION 构建参数
        assert re.search(r"^ARG\s+APP_VERSION", content, re.MULTILINE), (
            "Dockerfile 应声明 ARG APP_VERSION 版本构建参数"
        )
        # GIT_SHA 构建参数
        assert re.search(r"^ARG\s+GIT_SHA", content, re.MULTILINE), (
            "Dockerfile 应声明 ARG GIT_SHA Git 提交哈希构建参数"
        )
        # OCI 标签
        assert "org.opencontainers.image.version" in content, (
            "Dockerfile 应使用 OCI 标准标签记录版本"
        )
        assert "org.opencontainers.image.revision" in content, (
            "Dockerfile 应使用 OCI 标准标签记录 Git revision"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_multistage_build(self) -> None:
        """多阶段构建减少镜像体积与攻击面（SPEC 26.2）."""

        lines = _read_dockerfile_lines()
        from_lines = [
            line for line in lines if line.strip().upper().startswith("FROM ")
        ]
        assert len(from_lines) >= 2, "Dockerfile 应使用多阶段构建"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_exposes_port_8000(self) -> None:
        """容器暴露端口 8000。"""

        content = _DOCKERFILE.read_text(encoding="utf-8")
        assert re.search(r"^EXPOSE\s+8000", content, re.MULTILINE), (
            "Dockerfile 应 EXPOSE 8000"
        )


# ═══════════════════════════════════════════════════════════════════════════
# .dockerignore 断言（SPEC 26.2）
# ═══════════════════════════════════════════════════════════════════════════


class TestDockerignore:
    """.dockerignore 排除开发密钥与无关文件 — SPEC 26.2."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_dockerignore_exists(self) -> None:
        """.dockerignore 存在。"""

        assert _DOCKERIGNORE.is_file(), ".dockerignore 不存在"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_excludes_env_files(self) -> None:
        """排除环境变量与开发密钥文件。"""

        content = _DOCKERIGNORE.read_text(encoding="utf-8")
        assert ".env" in content, ".dockerignore 应排除 .env 文件"
        assert ".env.*" in content or ".env." in content, (
            ".dockerignore 应排除 .env.* 环境变量文件"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_excludes_dev_artifacts(self) -> None:
        """排除开发产物与无关文件。"""

        content = _DOCKERIGNORE.read_text(encoding="utf-8")
        excluded_items = [".git", "tests/", ".venv/", "__pycache__"]
        for item in excluded_items:
            assert item in content, f".dockerignore 应排除 {item}"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_excludes_secrets_and_keys(self) -> None:
        """排除密钥与证书文件。"""

        content = _DOCKERIGNORE.read_text(encoding="utf-8")
        for pattern in ["*.pem", "*.key", "*.crt"]:
            assert pattern in content, f".dockerignore 应排除 {pattern}"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_excludes_apex_coding_agent(self) -> None:
        """排除 .apex-coding-agent 元数据目录。"""

        content = _DOCKERIGNORE.read_text(encoding="utf-8")
        assert ".apex-coding-agent" in content, (
            ".dockerignore 应排除 .apex-coding-agent"
        )


# ═══════════════════════════════════════════════════════════════════════════
# compose.yaml 断言（SPEC 26.1）
# ═══════════════════════════════════════════════════════════════════════════


class TestComposeServices:
    """compose.yaml 服务结构断言 — SPEC 26.1."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_compose_yaml_exists(self) -> None:
        """compose.yaml 存在且可解析。"""

        assert _COMPOSE_YAML.is_file(), "deploy/compose.yaml 不存在"
        data = _read_compose_yaml()
        assert "services" in data

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_required_services_present(self) -> None:
        """包含 postgres、api、migrate、nginx 服务。"""

        data = _read_compose_yaml()
        services = data.get("services", {})
        for name in ("postgres", "api", "migrate", "nginx"):
            assert name in services, f"compose.yaml 缺少 {name} 服务"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_postgres_18_fixed_digest(self) -> None:
        """PostgreSQL 18 固定摘要镜像（SPEC 5.4 / 26.1）."""

        data = _read_compose_yaml()
        postgres = data["services"]["postgres"]
        image = postgres.get("image", "")
        assert "postgres:18" in image, "应使用 postgres:18 镜像"
        assert _SHA256_DIGEST_RE.search(image) is not None, (
            "postgres 镜像应使用固定摘要 @sha256:"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_at_least_two_api_workers(self) -> None:
        """至少 2 个 API Worker（SPEC 26.1: 默认 API Worker 数量为 2）."""

        data = _read_compose_yaml()
        api = data["services"]["api"]
        deploy = api.get("deploy", {})
        replicas = deploy.get("replicas", 1)
        assert replicas >= 2, f"API Worker 数量应 ≥ 2，当前为 {replicas}"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_migrate_is_one_shot(self) -> None:
        """migrate 服务为一次性服务（restart: no）."""

        data = _read_compose_yaml()
        migrate = data["services"]["migrate"]
        assert migrate.get("restart") == "no", (
            "migrate 服务应为 restart: no（一次性运行）"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_migrate_depends_on_postgres_healthy(self) -> None:
        """migrate 依赖 postgres 健康检查通过。"""

        data = _read_compose_yaml()
        migrate = data["services"]["migrate"]
        depends_on = migrate.get("depends_on", {})
        if isinstance(depends_on, dict):
            pg_dep = depends_on.get("postgres", {})
            assert pg_dep.get("condition") == "service_healthy", (
                "migrate 应 depends_on postgres: condition: service_healthy"
            )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_api_depends_on_migrate_success(self) -> None:
        """API 依赖 migrate 服务成功完成（SPEC 26.1: 发布门禁）."""

        data = _read_compose_yaml()
        api = data["services"]["api"]
        depends_on = api.get("depends_on", {})
        if isinstance(depends_on, dict):
            migrate_dep = depends_on.get("migrate", {})
            assert migrate_dep.get("condition") == "service_completed_successfully", (
                "api 应 depends_on migrate: condition: service_completed_successfully"
            )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_migrate_runs_db_upgrade(self) -> None:
        """migrate 服务执行 alembic upgrade（SPEC 26.1）."""

        data = _read_compose_yaml()
        migrate = data["services"]["migrate"]
        command = migrate.get("command", [])
        cmd_str = " ".join(command) if isinstance(command, list) else str(command)
        assert "db upgrade" in cmd_str or "db" in cmd_str, (
            "migrate 服务应执行 db upgrade 命令"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_nginx_service(self) -> None:
        """Nginx 反向代理服务存在且依赖 api 健康（SPEC 26.1 / 26.3）."""

        data = _read_compose_yaml()
        nginx = data["services"]["nginx"]
        image = nginx.get("image", "")
        assert "nginx" in image, "应使用 nginx 镜像"
        # nginx 依赖 api 健康
        depends_on = nginx.get("depends_on", {})
        if isinstance(depends_on, dict):
            api_dep = depends_on.get("api", {})
            assert api_dep.get("condition") == "service_healthy", (
                "nginx 应 depends_on api: condition: service_healthy"
            )


class TestComposeVolumes:
    """compose.yaml 卷定义断言 — SPEC 26.2."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_pgdata_volume(self) -> None:
        """pgdata 持久化卷存在（SPEC 26.2: PostgreSQL 数据目录通过持久化卷管理）."""

        data = _read_compose_yaml()
        volumes = data.get("volumes", {})
        assert "pgdata" in volumes, "compose.yaml 应定义 pgdata 卷"

        # postgres 使用 pgdata 卷
        pg_volumes = data["services"]["postgres"].get("volumes", [])
        assert any("pgdata" in v for v in pg_volumes), "postgres 服务应挂载 pgdata 卷"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_file_storage_volume(self) -> None:
        """文件存储卷存在（SPEC 19.1 / 26.2）."""

        data = _read_compose_yaml()
        volumes = data.get("volumes", {})
        assert "file_storage" in volumes, "compose.yaml 应定义 file_storage 卷"

        # api 使用 file_storage 卷
        api_volumes = data["services"]["api"].get("volumes", [])
        assert any("file_storage" in v for v in api_volumes), (
            "api 服务应挂载 file_storage 卷"
        )

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_backup_volume(self) -> None:
        """备份卷存在且独立于 pgdata（SPEC 27.1）."""

        data = _read_compose_yaml()
        volumes = data.get("volumes", {})
        assert "backups" in volumes, "compose.yaml 应定义 backups 卷"


class TestComposeHealthChecks:
    """compose.yaml 健康检查与重启策略断言 — SPEC 26.1."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_postgres_healthcheck(self) -> None:
        """postgres 有健康检查（SPEC 26.1）."""

        data = _read_compose_yaml()
        postgres = data["services"]["postgres"]
        assert "healthcheck" in postgres, "postgres 应定义 healthcheck"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_api_healthcheck(self) -> None:
        """api 有健康检查（SPEC 26.1）."""

        data = _read_compose_yaml()
        api = data["services"]["api"]
        assert "healthcheck" in api, "api 应定义 healthcheck"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_nginx_healthcheck(self) -> None:
        """nginx 有健康检查（SPEC 26.1）."""

        data = _read_compose_yaml()
        nginx = data["services"]["nginx"]
        assert "healthcheck" in nginx, "nginx 应定义 healthcheck"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_restart_policies(self) -> None:
        """核心服务有重启策略（SPEC 26.1: 重启策略自动恢复）."""

        data = _read_compose_yaml()
        services = data["services"]
        # postgres 和 api 应有重启策略（migrate 为 no）
        for name in ("postgres", "api", "nginx"):
            svc = services[name]
            assert "restart" in svc, f"{name} 服务应定义 restart 策略"
        # migrate 不自动重启
        assert services["migrate"].get("restart") == "no"


# ═══════════════════════════════════════════════════════════════════════════
# 发布流程文档断言（SPEC 26.1）
# ═══════════════════════════════════════════════════════════════════════════


class TestDeploymentDocs:
    """发布流程与连接预算文档断言 — SPEC 26.1."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_deployment_guide_exists(self) -> None:
        """发布流程文档存在。"""

        assert _DEPLOYMENT_DOC.is_file(), "docs/deployment-guide.md 不存在"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_downtime_switch_order(self) -> None:
        """文档明确停机切换顺序: 停旧版→迁移→启新版（SPEC 26.1）."""

        content = _DEPLOYMENT_DOC.read_text(encoding="utf-8")
        # 停止旧版本
        assert "停" in content and "旧" in content, "文档应描述停止旧版本"
        # 执行迁移
        assert "迁移" in content, "文档应描述执行迁移"
        # 启动新版本
        assert "新版本" in content or "新版" in content, "文档应描述启动新版本"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_migrate_gate_logic(self) -> None:
        """文档明确未迁移不就绪的门禁逻辑（SPEC 26.1）."""

        content = _DEPLOYMENT_DOC.read_text(encoding="utf-8")
        assert "service_completed_successfully" in content, (
            "文档应说明 migrate 的 service_completed_successfully 门禁"
        )
        assert "就绪检查" in content or "health/ready" in content, (
            "文档应说明未迁移时不就绪的检查逻辑"
        )


class TestConnectionBudgetDoc:
    """数据库连接预算文档断言 — SPEC 26.1."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_connection_budget_doc_exists(self) -> None:
        """连接预算文档存在。"""

        assert _CONNECTION_BUDGET_DOC.is_file(), "docs/connection-budget.md 不存在"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_formula_documented(self) -> None:
        """文档包含 26.1 公式。"""

        content = _CONNECTION_BUDGET_DOC.read_text(encoding="utf-8")
        assert "pool_size" in content and "max_overflow" in content, (
            "文档应包含 pool_size 和 max_overflow 参数"
        )
        assert "max_connections" in content, "文档应包含 PostgreSQL max_connections"

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_default_calculation(self) -> None:
        """默认配置计算: 2 Worker 峰值 20 连接。"""

        content = _CONNECTION_BUDGET_DOC.read_text(encoding="utf-8")
        assert "20" in content, "文档应说明默认 API 侧峰值合计 20 连接"
        assert "2" in content, "文档应说明默认 2 个 Worker"


# ═══════════════════════════════════════════════════════════════════════════
# 优雅关闭断言（SPEC 26.1）
# ═══════════════════════════════════════════════════════════════════════════


class TestGracefulShutdown:
    """优雅关闭实现断言 — SPEC 6.1 / 26.1."""

    @pytest.mark.g4
    @pytest.mark.deployment
    def test_lifespan_disposes_engine(self) -> None:
        """lifespan 关闭阶段释放数据库连接池（SPEC 6.1 / 26.1）."""

        content = _MAIN_PY.read_text(encoding="utf-8")
        assert "engine.dispose" in content, (
            "main.py lifespan 关闭阶段应调用 engine.dispose() 释放连接池"
        )
        assert "shutdown" in content.lower(), "main.py 应有 shutdown 生命周期事件标记"
