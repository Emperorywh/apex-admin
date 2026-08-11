# 备份调度接入 — SPEC 27.1

本文档描述如何在宿主机上通过 cron（Linux）或任务计划程序（Windows）接入每日备份与每月恢复演练。

> **注意**: Apex Admin 不内置自动调度器。备份调度由宿主机 cron/任务计划承载。

## 1. 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APEX_BACKUP_DAILY_RETENTION` | `7` | 日备份保留数量 |
| `APEX_BACKUP_WEEKLY_RETENTION` | `4` | 周备份保留数量 |
| `APEX_BACKUP_COPY_DIR` | `""` | 备份副本目录（**不得与应用同盘**） |
| `APEX_FILE_STORAGE_ROOT` | `./data/files` | 文件存储根目录 |
| `APEX_DATABASE_URL` | — | 源数据库连接 URL |

## 2. Linux cron 接入

### 2.1 每日备份

```cron
# 每日凌晨 2:00 执行数据库备份
# 备份失败时 cron 邮件通知 root（确保 MAILTO 已配置）
MAILTO=ops@example.com
0 2 * * * apex cd /opt/apex-admin && \
  APEX_DATABASE_URL="postgresql+psycopg://apex:SECRET@127.0.0.1:5432/apex" \
  APEX_ACCESS_TOKEN_HMAC_KEY="..." \
  APEX_REFRESH_TOKEN_HMAC_KEY="..." \
  APEX_SYSCONFIG_ENCRYPTION_KEY="..." \
  APEX_ENVIRONMENT=production \
  APEX_TRUSTED_HOSTS="admin.example.com" \
  APEX_ALLOWED_ORIGINS="https://admin.example.com" \
  APEX_METRICS_TOKEN="..." \
  uv run python -m app.cli backup create --output /backups \
  >> /var/log/apex-backup.log 2>&1
```

### 2.2 每月恢复演练

```cron
# 每月 1 日凌晨 3:00 执行恢复演练
MAILTO=ops@example.com
0 3 1 * * apex cd /opt/apex-admin && \
  APEX_DATABASE_URL="postgresql+psycopg://apex:SECRET@127.0.0.1:5432/apex" \
  APEX_ACCESS_TOKEN_HMAC_KEY="..." \
  APEX_REFRESH_TOKEN_HMAC_KEY="..." \
  APEX_SYSCONFIG_ENCRYPTION_KEY="..." \
  APEX_ENVIRONMENT=production \
  APEX_TRUSTED_HOSTS="admin.example.com" \
  APEX_ALLOWED_ORIGINS="https://admin.example.com" \
  APEX_METRICS_TOKEN="..." \
  uv run python -m app.cli backup verify \
    --backup-dir /backups \
    --report /backups/drill-$(date +\%Y\%m).json \
  >> /var/log/apex-backup-drill.log 2>&1
```

### 2.3 备份副本同步到异盘

```cron
# 每日凌晨 2:30 将备份同步到异盘副本目录
30 2 * * * apex rsync -a --delete /backups/ /mnt/backup-disk/apex-backups/
```

## 3. Windows 任务计划程序接入

### 3.1 每日备份

创建基本任务:
- **名称**: Apex Backup Daily
- **触发器**: 每天 02:00
- **操作**: 启动程序
  - **程序**: `C:\apex-admin\.venv\Scripts\python.exe`
  - **参数**: `-m app.cli backup create --output D:\backups`
  - **起始位置**: `C:\apex-admin`
  - **环境变量**: 通过批处理脚本设置（见下）

创建 `backup-daily.bat`:

```bat
@echo off
set APEX_ENVIRONMENT=production
set APEX_DATABASE_URL=postgresql+psycopg://apex:SECRET@127.0.0.1:5432/apex
set APEX_ACCESS_TOKEN_HMAC_KEY=...
set APEX_REFRESH_TOKEN_HMAC_KEY=...
set APEX_SYSCONFIG_ENCRYPTION_KEY=...
set APEX_TRUSTED_HOSTS=admin.example.com
set APEX_ALLOWED_ORIGINS=https://admin.example.com
set APEX_METRICS_TOKEN=...

cd /d C:\apex-admin
uv run python -m app.cli backup create --output D:\backups >> D:\logs\apex-backup.log 2>&1
if errorlevel 1 (
    echo [%date% %time%] 备份失败，退出码 %errorlevel% >> D:\logs\apex-backup-failed.log
)
```

### 3.2 每月恢复演练

创建 `backup-drill-monthly.bat`（类似上方），将命令替换为:

```bat
uv run python -m app.cli backup verify --backup-dir D:\backups --report D:\backups\drill-report.json
```

任务计划触发器设为每月 1 日 03:00。

## 4. 备份失败告警约定

### 4.1 退出码与日志

- `backup create` 成功: 退出码 0
- `backup create` 失败: 退出码非 0，结构化日志输出到标准错误
- cron 默认在命令返回非 0 时发送邮件（`MAILTO` 已配置）

### 4.2 告警建议

| 信号 | 条件 | 建议动作 |
| --- | --- | --- |
| cron 邮件 | `backup create` 退出码非 0 | 检查数据库连通性和磁盘空间 |
| 日志缺失 | 连续 24 小时无备份日志 | 检查 cron 服务状态 |
| 演练失败 | `backup verify` 退出码非 0 | 检查恢复流程和备份完整性 |
| RPO/RTO 超标 | 演练报告中 `overall_passed=false` | 评估备份频率和恢复流程 |

## 5. 异盘存储要求

**SPEC 27.1: 备份副本不得只保存在 PostgreSQL 数据卷或同一块物理磁盘中。**

- `APEX_BACKUP_COPY_DIR` 配置备份副本目录。
- 生产部署中，备份副本必须挂载到与 PostgreSQL 数据卷和应用文件存储不同的物理磁盘。
- Docker Compose 部署通过独立的 `backups` 卷承载备份（见 `deploy/compose.yaml`）。
- 宿主机部署通过 `rsync`（Linux）或 `robocopy`（Windows）将备份同步到异盘。
