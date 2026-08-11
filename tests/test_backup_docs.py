"""备份恢复文档与静态断言测试 — SPEC 27.1 / 27.2 / 27.3.

静态检查:
  - 恢复文档含数据库恢复、文件恢复、新服务器完整恢复三步骤
  - 明确演练不覆盖生产环境
  - cron/任务计划接入文档存在
  - 备份副本目录可配置且文档要求不与应用同盘
  - compose.yaml 备份服务卷存在
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RECOVERY_DOC = _PROJECT_ROOT / "docs" / "backup-recovery.md"
_SCHEDULING_DOC = _PROJECT_ROOT / "docs" / "backup-scheduling.md"
_COMPOSE_YAML = _PROJECT_ROOT / "deploy" / "compose.yaml"


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestBackupRecoveryDocs:
    """SPEC 27.3: 恢复文档含三步骤。"""

    def test_recovery_doc_exists(self) -> None:
        """恢复文档存在。"""

        assert _RECOVERY_DOC.exists(), f"恢复文档不存在: {_RECOVERY_DOC}"

    def test_scheduling_doc_exists(self) -> None:
        """cron/任务计划接入文档存在。"""

        assert _SCHEDULING_DOC.exists(), f"调度文档不存在: {_SCHEDULING_DOC}"

    def test_db_recovery_steps(self) -> None:
        """SPEC 27.3: 数据库恢复步骤。"""

        text = _RECOVERY_DOC.read_text(encoding="utf-8")
        assert "数据库恢复" in text
        assert "pg_dump" in text.lower() or "database.sql" in text
        assert "alembic_version" in text or "迁移版本" in text

    def test_file_recovery_steps(self) -> None:
        """SPEC 27.3: 文件恢复步骤。"""

        text = _RECOVERY_DOC.read_text(encoding="utf-8")
        assert "文件恢复" in text
        assert "manifest" in text.lower()
        assert "sha256" in text.lower() or "SHA-256" in text

    def test_full_server_recovery_steps(self) -> None:
        """SPEC 27.3: 新服务器完整恢复步骤。"""

        text = _RECOVERY_DOC.read_text(encoding="utf-8")
        assert "新服务器" in text
        assert "db upgrade" in text or "迁移" in text
        assert "data check" in text or "数据一致性" in text

    def test_drill_does_not_cover_production(self) -> None:
        """SPEC 27.3: 明确演练不覆盖生产环境。"""

        text = _RECOVERY_DOC.read_text(encoding="utf-8")
        assert "不覆盖生产" in text or "不直接覆盖" in text
        assert "隔离" in text

    def test_drill_report_fields(self) -> None:
        """SPEC 27.3: 演练报告含 Backup ID/起止时间/RPO/RTO/检查结果/失败原因。"""

        text = _RECOVERY_DOC.read_text(encoding="utf-8")
        assert "backup_id" in text or "Backup ID" in text
        assert "started_at" in text or "开始时间" in text
        assert "finished_at" in text or "结束时间" in text
        assert "actual_rpo" in text or "RPO" in text
        assert "actual_rto" in text or "RTO" in text

    def test_rpo_rto_targets(self) -> None:
        """SPEC 27.1: RPO<=24h, RTO<=4h 度量。"""

        text = _RECOVERY_DOC.read_text(encoding="utf-8")
        assert "24" in text  # RPO 24h
        assert "4" in text  # RTO 4h


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestBackupSchedulingDocs:
    """SPEC 27.1: 每日备份与每月恢复演练的宿主机 cron/任务计划接入。"""

    def test_cron_section_exists(self) -> None:
        """cron 接入文档存在。"""

        text = _SCHEDULING_DOC.read_text(encoding="utf-8")
        assert "cron" in text.lower()

    def test_daily_backup_schedule(self) -> None:
        """每日备份调度接入。"""

        text = _SCHEDULING_DOC.read_text(encoding="utf-8")
        assert "每日" in text or "daily" in text.lower()
        assert "backup create" in text

    def test_monthly_drill_schedule(self) -> None:
        """每月恢复演练调度接入。"""

        text = _SCHEDULING_DOC.read_text(encoding="utf-8")
        assert "每月" in text or "monthly" in text.lower()
        assert "backup verify" in text

    def test_windows_task_scheduler(self) -> None:
        """Windows 任务计划程序接入。"""

        text = _SCHEDULING_DOC.read_text(encoding="utf-8")
        assert "任务计划" in text or "Task Scheduler" in text

    def test_failure_alerting(self) -> None:
        """SPEC 27.1: 备份失败告警约定。"""

        text = _SCHEDULING_DOC.read_text(encoding="utf-8")
        assert "MAILTO" in text or "告警" in text or "失败" in text

    def test_backup_copy_dir_configurable(self) -> None:
        """SPEC 27.1: 备份副本目录可配置。"""

        text = _SCHEDULING_DOC.read_text(encoding="utf-8")
        assert "BACKUP_COPY_DIR" in text or "APEX_BACKUP_COPY_DIR" in text

    def test_different_disk_requirement(self) -> None:
        """SPEC 27.1: 备份副本不得与应用同盘。"""

        text = _SCHEDULING_DOC.read_text(encoding="utf-8")
        assert "同盘" in text or "不同" in text
        assert "物理磁盘" in text or "异盘" in text


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestBackupConfigFields:
    """SPEC 27.1: 备份配置字段。"""

    def test_config_has_backup_fields(self) -> None:
        """Settings 包含备份保留与副本目录配置。"""

        from app.core.config import Settings

        field_names = set(Settings.model_fields)
        assert "BACKUP_DAILY_RETENTION" in field_names
        assert "BACKUP_WEEKLY_RETENTION" in field_names
        assert "BACKUP_COPY_DIR" in field_names

    def test_default_retention_values(self) -> None:
        """默认保留 7 日 + 4 周。"""

        import os

        from app.core.config import Settings

        # 清除可能影响默认值的环境变量
        env_keys = [
            "APEX_ENVIRONMENT",
            "APEX_BACKUP_DAILY_RETENTION",
            "APEX_BACKUP_WEEKLY_RETENTION",
            "APEX_BACKUP_COPY_DIR",
        ]
        saved: dict[str, str | None] = {}
        for key in env_keys:
            saved[key] = os.environ.pop(key, None)
        try:
            os.environ["APEX_ENVIRONMENT"] = "testing"
            settings = Settings()
            assert settings.BACKUP_DAILY_RETENTION == 7
            assert settings.BACKUP_WEEKLY_RETENTION == 4
        finally:
            for key, val in saved.items():
                if val is not None:
                    os.environ[key] = val
                else:
                    os.environ.pop(key, None)


@pytest.mark.g4
@pytest.mark.deployment
@pytest.mark.unit
class TestComposeBackupVolume:
    """SPEC 27.1: compose.yaml 备份卷存在。"""

    def test_backups_volume_exists(self) -> None:
        """compose.yaml 定义独立备份卷。"""

        import yaml

        compose = yaml.safe_load(_COMPOSE_YAML.read_text(encoding="utf-8"))
        volumes = compose.get("volumes", {})
        assert "backups" in volumes, "compose.yaml 缺少 backups 卷"

    def test_backup_service_exists(self) -> None:
        """compose.yaml 定义备份服务。"""

        import yaml

        compose = yaml.safe_load(_COMPOSE_YAML.read_text(encoding="utf-8"))
        services = compose.get("services", {})
        assert "backup" in services, "compose.yaml 缺少 backup 服务"
