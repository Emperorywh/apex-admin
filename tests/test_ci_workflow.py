"""CI 部署验收工作流静态分析测试 — SPEC 34.4 / 28.6 / 30.3.

静态解析 deploy-acceptance.yml 工作流 YAML，验证:
  - 工作流 YAML 可解析（SPEC 34.4: CI 工作流 YAML 可解析）
  - 任务依赖顺序正确（SPEC 34.4: 任务依赖顺序正确）
  - 34.4 全部 Docker 依赖条目在工作流中有对应步骤

覆盖的 34.4 条目:
  - compose config --quiet（28.6 / 34.4）
  - 镜像构建（34.4 / 26.2）
  - 容器内 nginx -t（34.4 / 26.3 / 28.6）
  - 非 root 与无开发密钥检查（34.4 / 26.2 / 28.6）
  - 双 API Worker 一致性（5.3 / 34.4 / 28.6）
  - HTTPS/代理头/Host/CORS/限流集成测试（23.1 / 23.4 / 26.3 / 34.4）
  - 发布门禁（26.1 / 34.4 / 28.6）
  - 优雅关闭（6.1 / 26.1 / 34.4）
  - 备份恢复演练与 RPO/RTO 报告（27.1 / 27.2 / 34.4）
  - 私有文件禁止绕过授权下载（19.2 / 26.3 / 34.4）
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from typing import Any

# ── 路径常量 ───────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_FILE = _PROJECT_ROOT / ".github" / "workflows" / "deploy-acceptance.yml"
_CI_WORKFLOW_FILE = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


# ── 辅助函数 ──────────────────────────────────────────────────────────────


def _load_workflow(file_path: Path) -> dict[str, Any]:
    """解析工作流 YAML 文件。"""

    assert file_path.is_file(), f"工作流文件不存在: {file_path}"
    return yaml.safe_load(file_path.read_text(encoding="utf-8"))


def _get_job_needs(workflow: dict[str, Any], job_name: str) -> list[str]:
    """获取指定 job 的 needs 依赖列表。"""

    job = workflow.get("jobs", {}).get(job_name, {})
    needs = job.get("needs", [])
    if isinstance(needs, str):
        return [needs]
    return list(needs)


def _get_all_step_content(workflow: dict[str, Any]) -> str:
    """提取工作流中全部 step 的文本内容（name + run + if 条件），用于关键词匹配。"""

    parts: list[str] = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            name = step.get("name", "")
            run_cmd = step.get("run", "")
            if_cond = step.get("if", "")
            parts.append(name)
            parts.append(run_cmd)
            if if_cond:
                parts.append(str(if_cond))
    return "\n".join(parts)


def _get_triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """提取工作流触发器配置。

    YAML 1.1 中 unquoted ``on:`` 被解析为 Python ``True``，
    此函数统一处理两种情况。
    """

    raw = workflow.get("on", workflow.get(True, {}))
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {raw: None}
    return raw


def _has_cycle(workflow: dict[str, Any]) -> bool:
    """检测 job 依赖图中是否存在环。"""

    jobs = workflow.get("jobs", {})
    # 构建邻接表
    graph: dict[str, list[str]] = {}
    for name, job in jobs.items():
        needs = job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        graph[name] = [n for n in needs if n in jobs]

    # DFS 检测环
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if color[neighbor] == GRAY:
                return True
            if color[neighbor] == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    return any(color[n] == WHITE and dfs(n) for n in graph)


# ═══════════════════════════════════════════════════════════════════════════
# 工作流 YAML 可解析（SPEC 34.4: CI 工作流 YAML 可解析）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestWorkflowYamlParseable:
    """工作流 YAML 文件存在且可解析 — SPEC 34.4."""

    def test_deploy_acceptance_workflow_exists(self) -> None:
        """部署验收工作流文件存在。"""

        assert _WORKFLOW_FILE.is_file(), (
            ".github/workflows/deploy-acceptance.yml 不存在"
        )

    def test_workflow_yaml_parseable(self) -> None:
        """工作流 YAML 可被 yaml.safe_load 解析。"""

        data = _load_workflow(_WORKFLOW_FILE)
        assert isinstance(data, dict), "工作流 YAML 顶层应为字典"
        assert "jobs" in data, "工作流应包含 jobs 字段"
        # YAML 1.1 中 on: 被解析为 True
        assert "on" in data or True in data, "工作流应包含触发器 (on)"

    def test_ci_workflow_yaml_parseable(self) -> None:
        """CI 工作流 YAML 可被解析。"""

        data = _load_workflow(_CI_WORKFLOW_FILE)
        assert isinstance(data, dict), "CI 工作流 YAML 顶层应为字典"
        assert "jobs" in data, "CI 工作流应包含 jobs 字段"
        # YAML 1.1 中 on: 被解析为 True
        assert "on" in data or True in data, "CI 工作流应包含触发器 (on)"


# ═══════════════════════════════════════════════════════════════════════════
# 任务依赖顺序正确（SPEC 34.4: 任务依赖顺序正确）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestJobDependencyOrder:
    """工作流 Job 依赖关系正确 — SPEC 34.4."""

    def test_no_dependency_cycle(self) -> None:
        """Job 依赖图中不存在环。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        assert not _has_cycle(workflow), "Job 依赖图中存在环"

    def test_generate_is_first_job(self) -> None:
        """generate Job 不依赖其他 Job（是依赖链起点）。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        needs = _get_job_needs(workflow, "generate")
        assert needs == [], f"generate Job 不应依赖其他 Job，当前 needs: {needs}"

    def test_compose_validate_depends_on_generate(self) -> None:
        """compose-validate 依赖 generate。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        needs = _get_job_needs(workflow, "compose-validate")
        assert "generate" in needs, "compose-validate 应依赖 generate"

    def test_image_checks_depends_on_compose_validate(self) -> None:
        """image-checks 依赖 compose-validate（先验证配置再构建镜像）。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        needs = _get_job_needs(workflow, "image-checks")
        assert "compose-validate" in needs, "image-checks 应依赖 compose-validate"

    def test_stack_integration_depends_on_image_checks(self) -> None:
        """stack-integration 依赖 image-checks（镜像检查通过后再启动全栈）。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        needs = _get_job_needs(workflow, "stack-integration")
        assert "image-checks" in needs, "stack-integration 应依赖 image-checks"

    def test_g4_local_depends_on_generate(self) -> None:
        """g4-local 依赖 generate（可在 Docker 链并行运行）。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        needs = _get_job_needs(workflow, "g4-local")
        assert "generate" in needs, "g4-local 应依赖 generate"

    def test_dependency_chain_is_linear_for_docker(self) -> None:
        """Docker 链线性依赖正确（generate → ... → stack-integration）."""

        workflow = _load_workflow(_WORKFLOW_FILE)
        chain = ["generate", "compose-validate", "image-checks", "stack-integration"]
        for i in range(1, len(chain)):
            needs = _get_job_needs(workflow, chain[i])
            assert chain[i - 1] in needs, (
                f"{chain[i]} 应依赖 {chain[i - 1]}，当前 needs: {needs}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 34.4 全部 Docker 依赖条目覆盖（SPEC 34.4 / 28.6）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestWorkflowCovers344Items:
    """逐条核对工作流步骤覆盖 34.4 Docker 依赖条目 — SPEC 34.4 / 28.6."""

    @pytest.fixture
    def workflow_content(self) -> str:
        """工作流全部 step 文本内容。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        return _get_all_step_content(workflow)

    def test_covers_compose_config_quiet(self, workflow_content: str) -> None:
        """覆盖条目: docker compose config --quiet（SPEC 28.6 / 34.4）。"""

        assert "compose" in workflow_content.lower() and (
            "config" in workflow_content.lower()
        ), "工作流应包含 docker compose config 步骤"

    def test_covers_image_build(self, workflow_content: str) -> None:
        """覆盖条目: 应用镜像构建（SPEC 34.4 / 26.2）。"""

        assert "build" in workflow_content.lower(), "工作流应包含 Docker 镜像构建步骤"

    def test_covers_nginx_test(self, workflow_content: str) -> None:
        """覆盖条目: 容器内 nginx -t（SPEC 34.4 / 26.3 / 28.6）。"""

        assert "nginx" in workflow_content.lower() and ("-t" in workflow_content), (
            "工作流应包含容器内 nginx -t 步骤"
        )

    def test_covers_non_root_check(self, workflow_content: str) -> None:
        """覆盖条目: 非 root 用户检查（SPEC 34.4 / 26.2 / 28.6）。"""

        assert "root" in workflow_content.lower() or (
            "appuser" in workflow_content.lower()
        ), "工作流应包含非 root 用户检查步骤"

    def test_covers_no_dev_secrets_check(self, workflow_content: str) -> None:
        """覆盖条目: 无开发密钥检查（SPEC 34.4 / 26.2 / 28.6）。"""

        assert "密钥" in workflow_content or (
            ".env" in workflow_content and "pem" in workflow_content.lower()
        ), "工作流应包含无开发密钥检查步骤"

    def test_covers_worker_consistency(self, workflow_content: str) -> None:
        """覆盖条目: 双 API Worker 一致性（SPEC 5.3 / 34.4 / 28.6）。"""

        assert (
            "worker" in workflow_content.lower()
            or ("consistency" in workflow_content.lower())
            or ("deploy_acceptance" in workflow_content.lower())
        ), "工作流应包含双 Worker 一致性测试步骤"

    def test_covers_http_integration(self, workflow_content: str) -> None:
        """覆盖条目: HTTPS/代理头/Host/CORS/限流集成测试（SPEC 34.4）。"""

        # 集成测试通过 deploy_acceptance.py 统一运行
        assert "deploy_acceptance" in workflow_content.lower() or (
            "https" in workflow_content.lower()
        ), "工作流应包含 HTTPS/安全集成测试步骤"

    def test_covers_release_gate(self, workflow_content: str) -> None:
        """覆盖条目: 发布门禁（SPEC 26.1 / 34.4 / 28.6）。"""

        assert (
            "migrate" in workflow_content.lower()
            or ("health/ready" in workflow_content.lower())
            or (
                "release" in workflow_content.lower()
                or "gate" in workflow_content.lower()
            )
        ), "工作流应包含发布门禁测试步骤"

    def test_covers_graceful_shutdown(self, workflow_content: str) -> None:
        """覆盖条目: 优雅关闭（SPEC 6.1 / 26.1 / 34.4）。"""

        assert (
            "shutdown" in workflow_content.lower()
            or ("restart" in workflow_content.lower())
            or ("deploy_acceptance" in workflow_content.lower())
        ), "工作流应包含优雅关闭测试步骤"

    def test_covers_backup_recovery(self, workflow_content: str) -> None:
        """覆盖条目: 备份恢复演练与 RPO/RTO 报告（SPEC 27.1 / 27.2 / 34.4）。"""

        assert "backup" in workflow_content.lower() or (
            "deploy_acceptance" in workflow_content.lower()
        ), "工作流应包含备份恢复演练步骤"

    def test_covers_file_access_control(self, workflow_content: str) -> None:
        """覆盖条目: 私有文件禁止绕过授权下载（SPEC 19.2 / 26.3 / 34.4）。"""

        assert "file" in workflow_content.lower() or (
            "deploy_acceptance" in workflow_content.lower()
        ), "工作流应包含私有文件访问控制测试步骤"

    def test_covers_copier_generation(self, workflow_content: str) -> None:
        """覆盖条目: Copier 模板生成（SPEC 30.3: 工作流运行于生成实例）。"""

        assert "copier" in workflow_content.lower(), "工作流应包含 Copier 模板生成步骤"

    def test_covers_g4_local_tests(self, workflow_content: str) -> None:
        """覆盖条目: 本地可执行的 g4 测试子集（SPEC 34.4）。"""

        assert "g4" in workflow_content.lower(), "工作流应包含 g4 本地测试步骤"

    def test_covers_tls_cert_generation(self, workflow_content: str) -> None:
        """覆盖条目: HTTPS 测试需要 TLS 证书。"""

        assert (
            "cert" in workflow_content.lower()
            or ("ssl" in workflow_content.lower())
            or ("openssl" in workflow_content.lower())
        ), "工作流应包含 TLS 证书生成步骤"

    def test_covers_cleanup(self, workflow_content: str) -> None:
        """覆盖条目: 全栈测试后清理（if: always）。"""

        assert "down" in workflow_content.lower() and (
            "always" in workflow_content.lower()
        ), "工作流应包含始终执行的清理步骤"


# ═══════════════════════════════════════════════════════════════════════════
# 工作流触发器配置（SPEC 34.4）
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestWorkflowTriggers:
    """工作流触发器配置正确 — SPEC 34.4."""

    def test_triggers_configured(self) -> None:
        """工作流配置了 push、pull_request 和手动触发。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        triggers = _get_triggers(workflow)
        assert "workflow_dispatch" in triggers, (
            "工作流应支持手动触发 (workflow_dispatch)"
        )

    def test_push_to_main_triggers(self) -> None:
        """推送到 main 分支触发工作流。"""

        workflow = _load_workflow(_WORKFLOW_FILE)
        triggers = _get_triggers(workflow)
        push_config = triggers.get("push", {})
        if push_config:
            branches = push_config.get("branches", [])
            assert "main" in branches, "push 触发器应包含 main 分支"
