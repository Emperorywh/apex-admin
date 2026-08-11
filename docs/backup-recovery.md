# 备份与恢复指南 — SPEC 27.1 / 27.2 / 27.3

本文档定义 Apex Admin 的备份策略、恢复流程和演练规范。

## 1. 备份策略

### 1.1 数据库备份

- **频率**: 每天至少执行一次 PostgreSQL 逻辑全量备份（`pg_dump --format=plain`）。
- **RPO 目标**: 不超过 24 小时。
- **RTO 目标**: 不超过 4 小时。
- **保留策略**: 至少保留最近 7 个日备份和最近 4 个周备份。滚动清理由 `backup create` 命令自动执行。

### 1.2 文件备份

- 只备份 `READY` 状态的文件（SPEC 19.3）。
- 临时文件、`PENDING`、`FAILED`、`DELETING` 和 `DELETED` 文件不进入正式备份。
- 文件物理删除至少延迟 7 天（由 19.3 的 `DELETING` 延迟删除规则实现），确保备份窗口内清单引用的文件仍可复制。
- 每个备份集具有唯一 Backup ID、文件清单（文件数量、总大小）和清单 SHA-256 哈希（防篡改）。

### 1.3 备份副本存储

- **SPEC 27.1: 备份副本不得只保存在 PostgreSQL 数据卷或同一块物理磁盘中。**
- 备份副本目录通过 `APEX_BACKUP_COPY_DIR` 配置。
- 生产环境中，备份副本必须挂载在与应用和数据库不同的物理磁盘上。
- 敏感备份受到访问控制（目录权限仅限运维用户）和必要的加密保护。

### 1.4 备份失败发现

- `backup create` 命令失败时退出码非 0。
- 失败信息通过结构化日志输出到标准错误。
- cron/任务计划邮件或告警约定见 [备份调度接入](backup-scheduling.md)。

## 2. 恢复流程

> **重要约束**: 恢复演练不会直接覆盖生产环境。所有演练在隔离数据库上执行。

### 2.1 步骤一: 数据库恢复

```bash
# 1. 确认备份集目录（包含 manifest.json 和 database.sql）
ls backups/backup-YYYYMMDD-HHMMSS-xxxxxxxx/

# 2. 创建隔离数据库（演练用）
psql -h <host> -U <user> -d postgres -c 'CREATE DATABASE apex_restore'

# 3. 恢复 pg_dump 输出到隔离库
psql -h <host> -U <user> -d apex_restore \
  -v ON_ERROR_STOP=1 \
  -f backups/backup-YYYYMMDD-HHMMSS-xxxxxxxx/database.sql

# 4. 验证迁移版本一致
psql -h <host> -U <user> -d apex_restore \
  -c 'SELECT version_num FROM alembic_version'

# 5. 确认迁移版本与源库 head 一致后，
#    按运维流程将应用指向恢复库或替换生产库
```

### 2.2 步骤二: 文件恢复

```bash
# 1. 确认备份集文件清单
cat backups/backup-YYYYMMDD-HHMMSS-xxxxxxxx/manifest.json | python -m json.tool

# 2. 将备份的 READY 文件恢复到文件存储目录
cp -p backups/backup-YYYYMMDD-HHMMSS-xxxxxxxx/files/* /app/data/files/files/

# 3. 验证文件一致性（SHA-256）
#    manifest.json 中每个文件条目含 sha256 字段，逐一校验:
sha256sum /app/data/files/files/<storage_name>
```

### 2.3 步骤三: 新服务器完整恢复

```bash
# 1. 在新服务器上准备运行环境
#    - 安装 Python 3.13 + uv
#    - 安装 PostgreSQL 18
#    - 克隆代码仓库并安装依赖
uv sync --frozen --no-dev

# 2. 配置环境变量（生产安全配置）
cp deploy/.env.example .env.production
# 编辑 .env.production，填写生产密钥、数据库连接等

# 3. 创建并恢复数据库
createdb -h <host> -U <user> apex
psql -h <host> -U <user> -d apex \
  -v ON_ERROR_STOP=1 \
  -f backups/backup-YYYYMMDD-HHMMSS-xxxxxxxx/database.sql

# 4. 恢复文件
mkdir -p /app/data/files/files
cp -p backups/backup-YYYYMMDD-HHMMSS-xxxxxxxx/files/* /app/data/files/files/

# 5. 执行迁移确认（确保 schema 与代码版本一致）
uv run python -m app.cli db upgrade

# 6. 执行数据一致性检查
uv run python -m app.cli data check

# 7. 执行健康检查
uv run python -m app.cli db check

# 8. 启动应用
docker compose --env-file .env.production up -d
```

## 3. 恢复演练

### 3.1 自动演练命令

```bash
# 创建备份
uv run python -m app.cli backup create --output /backups

# 验证最新备份（恢复到隔离库，执行检查，生成报告）
uv run python -m app.cli backup verify \
  --backup-dir /backups \
  --report /backups/drill-report.json
```

### 3.2 演练报告字段

演练报告（`report.json`）包含以下结构化字段:

| 字段 | 说明 |
| --- | --- |
| `backup_id` | 被验证的备份集唯一 ID |
| `started_at` | 演练开始时间（UTC ISO 8601） |
| `finished_at` | 演练结束时间（UTC ISO 8601） |
| `actual_rpo_hours` | 实际 RPO — 备份创建到演练开始的时间差（小时） |
| `actual_rto_hours` | 实际 RTO — 演练恢复耗时（小时） |
| `rpo_target_hours` | RPO 目标（24 小时） |
| `rto_target_hours` | RTO 目标（4 小时） |
| `checks.migration_version` | 迁移版本检查结果 |
| `checks.data_integrity` | 数据完整性检查结果 |
| `checks.file_consistency` | 文件一致性检查结果 |
| `overall_passed` | 全部检查是否通过且 RPO/RTO 未超标 |
| `failure_reason` | 失败原因（通过时为 null） |

### 3.3 演练约束

- **演练不覆盖生产环境**: 验证命令在隔离数据库（`apex_verify_*`）上执行恢复，不修改源数据库。
- **RPO/RTO 超标即验收失败**: 实际 RPO 超过 24 小时或实际 RTO 超过 4 小时，`overall_passed` 为 `false`。
- **恢复完成后执行检查**: 迁移版本检查、数据完整性检查、文件一致性检查三项全通过才算成功。
- **每月至少演练一次**: 通过 cron/任务计划自动触发（见备份调度接入文档）。

### 3.4 检查项说明

| 检查 | 说明 |
| --- | --- |
| 迁移版本 | 恢复库的 `alembic_version` 必须与源库 head revision 一致 |
| 数据完整性 | 执行 `data check` — 检测菜单/部门循环、孤立关联数据 |
| 文件一致性 | 备份目录中每个文件的 SHA-256 必须与 manifest.json 中的哈希匹配 |
