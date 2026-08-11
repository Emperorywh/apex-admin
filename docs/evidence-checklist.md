# G4 Production Ready 验收证据清单 — SPEC 34.4

> 逐条登记 SPEC 34.4 全部验收条目的证据状态。
> 本地可验证项标注绿色通过；CI 依赖项标注工作流名称与手动确认状态。
> 对应 TASK-036 验收条件 2。

验收日期：2026-08-12
SPEC 版本：9de44cc2b96c129ee0adeb0be142242a4052a4653a00dc94d44564aa47a58637

---

## 证据状态图例

| 标记 | 含义 |
| --- | --- |
| ✅ 本地通过 | 在本地环境执行并通过（退出码 0） |
| 🔲 CI 待确认 | 需在 GitHub Actions 中确认通过 |
| ⏳ 手动确认 | 需用户手动审阅确认 |

---

## 34.4 验收条目

### 条目 1：34.1、34.2 和 34.3 全部通过

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| G1 验收（34.1 全部 12 条） | `pytest -m "g1 or g2 or g3"` 1283 passed / 2 skipped（Windows 符号链接）；覆盖率语句 91.94% ≥ 85%、分支 80.27% ≥ 80%；安全模块 90% 双门槛达标 | ✅ 本地通过 |
| G2 验收（34.2 全部 12 条） | 294 个 g2 测试通过；认证/会话/权限/审计模块覆盖率 ≥ 90%；防枚举、并发刷新吊销、越权保护、审计同事务全部覆盖 | ✅ 本地通过 |
| G3 验收（34.3 全部 12 条） | 524 个 g3 测试通过；种子幂等、循环防护、菜单可见性≠授权、文件状态机全转换、故障注入、跨模块边界隔离全部覆盖 | ✅ 本地通过 |

---

### 条目 2：`uv run pytest -m g4` 返回 0

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| G4 本地子集 | `pytest -m "g4 and not integration"` 199 passed；覆盖 HTTP 安全、生产启动校验、生产日志、指标、部署静态断言、Nginx 静态断言、CI 工作流分析、备份文档断言 | ✅ 本地通过 |
| G4 全量（含 integration） | backup integration 测试需 pg_dump + 真实数据库，在 CI 环境（postgres:18 容器含 pg_dump）中执行 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → g4-local） |

---

### 条目 3：Copier 默认答案生成临时项目验证

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| `uv sync --frozen` | verify_generated.py --gate g123 在生成项目中执行 uv sync --frozen 退出码 0 | ✅ 本地通过 |
| 数据库迁移 | 生成项目中 alembic upgrade head 成功，alembic heads 输出恰好一个 head | ✅ 本地通过 |
| G1-G3 测试 | 生成项目中 `pytest -m "g1 or g2 or g3"` 全部通过 | ✅ 本地通过 |
| 标识残留检查 | 生成项目中无原基座项目标识残留（项目名/配置前缀/URN 命名空间） | ✅ 本地通过 |
| G4 本地子集在生成项目中通过 | `verify_generated.py --gate g4` 执行 `(g1 or g2 or g3) or (g4 and not integration)` | ✅ 本地通过 |

---

### 条目 4：`docker compose config --quiet`、镜像构建、容器内 `nginx -t`

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| compose config | 本机无 Docker；静态断言测试验证 compose.yaml 结构（35 个断言：必需服务、固定摘要、卷、健康检查、重启策略） | ✅ 本地通过（静态） |
| compose config --quiet | 需 Docker 环境 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → compose-validate） |
| 镜像构建 | 静态断言验证 Dockerfile 指令（多阶段、固定摘要基础镜像、uv 冻结安装、非 root、版本构建参数） | ✅ 本地通过（静态） |
| 镜像构建（实际） | 需 Docker 环境 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → image-checks） |
| nginx -t | 需 Docker 环境；29 项 Nginx 静态 lint 校验在本地通过 | ✅ 本地通过（静态 lint） |
| nginx -t（实际） | 需 Docker 环境 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → image-checks） |

---

### 条目 5：Docker Compose 单机全栈启动

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| ≥2 API Worker | compose.yaml `deploy.replicas: 2` 静态断言通过 | ✅ 本地通过（静态） |
| 全栈启动 | 需 Docker 环境 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → stack-integration） |
| 容器健康状态 | compose.yaml 全部服务配置健康检查 | ✅ 本地通过（静态） |

---

### 条目 6：非 root 运行、仅 Nginx 对外、私有文件不可绕过授权

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| 非 root 用户 | Dockerfile `USER appuser` 静态断言通过；.dockerignore 排除 .env/密钥/测试 | ✅ 本地通过（静态） |
| 非 root（实际） | 需 Docker 环境 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → image-checks） |
| 仅 Nginx 对外 | compose.yaml 仅 Nginx 映射端口；API 不映射宿主端口 | ✅ 本地通过（静态） |
| 私有文件不可绕过 | Nginx 无 root/alias 静态直出；文件经应用授权后 StreamingResponse 返回 | ✅ 本地通过（静态 + 测试） |
| 私有文件（实际） | 需 Docker 环境 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → stack-integration） |

---

### 条目 7：HTTPS、可信代理头、Host 白名单、CORS、请求体限制、限流

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| HTTPS 终结 | Nginx 静态断言验证 listen 443 ssl + HTTP→HTTPS 301 | ✅ 本地通过（静态） |
| 可信代理头 | Nginx 静态断言验证 X-Real-IP/X-Forwarded-For/X-Forwarded-Proto；TrustedProxyMiddleware 测试通过 | ✅ 本地通过 |
| Host 白名单 | 生产环境 TRUSTED_HOSTS 通配/缺白名单启动失败测试通过 | ✅ 本地通过 |
| CORS | 生产环境 CORS 通配/缺白名单启动失败测试通过 | ✅ 本地通过 |
| 请求体限制 | 常规 1MB / 上传 50MB RequestBodySizeMiddleware 测试通过 | ✅ 本地通过 |
| 登录限流 | Nginx 10r/m limit_req zone + 规则静态断言通过 | ✅ 本地通过（静态） |
| 刷新限流 | Nginx 30r/m limit_req zone + 规则静态断言通过 | ✅ 本地通过（静态） |
| 端到端集成 | 需 Docker 全栈环境 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → stack-integration） |

---

### 条目 8：优雅关闭

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| Lifespan 释放连接池 | main.py lifespan 关闭钩子调用 `engine.dispose()`；连接预算文档说明 2×(5+5)=20 ≤ 100 | ✅ 本地通过 |
| 优雅关闭集成测试 | 需 Docker 环境（SIGTERM → 不接受新请求 → 进行中请求完成 → 连接释放） | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → stack-integration） |

---

### 条目 9：备份任务生成完整备份集

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| Backup ID | BackupSet 模型含 backup_id（UUID4），备份文档断言通过 | ✅ 本地通过 |
| 数据库文件 | pg_dump 逻辑全量备份；service.py `_run_pg_dump` 实现 | ✅ 本地通过（代码审查） |
| READY 文件清单 | `build_file_manifest` 查询 READY 文件并构建清单条目；备份文档断言通过 | ✅ 本地通过 |
| 哈希 | `compute_manifest_hash` 计算清单 SHA-256 哈希；备份文档断言通过 | ✅ 本地通过 |
| 备份创建集成测试 | 需 pg_dump + 真实 PostgreSQL | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → g4-local 或 stack-integration） |

---

### 条目 10：隔离环境恢复 + RPO/RTO 报告

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| 隔离库恢复 | `verify_backup` 创建隔离数据库恢复 pg_dump 输出 | ✅ 本地通过（代码审查） |
| 迁移版本检查 | verify 检查隔离库迁移版本与主库一致 | ✅ 本地通过（代码审查） |
| 数据完整性检查 | verify 检查恢复后的数据完整性 | ✅ 本地通过（代码审查） |
| 文件一致性检查 | verify 检查文件清单哈希一致 | ✅ 本地通过（代码审查） |
| 结构化演练报告 | BackupReport 含 backup_id/起止时间/实际 RPO/实际 RTO/检查结果 | ✅ 本地通过 |
| RPO ≤ 24h | RPO_TARGET_HOURS = 24.0，报告计算实际 RPO | ✅ 本地通过（代码审查） |
| RTO ≤ 4h | RTO_TARGET_HOURS = 4.0，报告计算实际 RTO | ✅ 本地通过（代码审查） |
| 恢复演练集成测试 | 需 pg_dump + pg_restore + 真实 PostgreSQL | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → stack-integration） |

---

### 条目 11：恢复后 data check、files reconcile --dry-run、健康检查

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| data check | `admin data check` 命令实现，7 项检查（循环/孤立关系/失效关联） | ✅ 本地通过 |
| files reconcile --dry-run | `files reconcile --dry-run` 不修改数据测试通过 | ✅ 本地通过 |
| 健康检查 | `/health/live` 返回 200、`/health/ready` 返回 503/200 测试通过 | ✅ 本地通过 |
| 恢复后三项检查 | 需 Docker 全栈 + 备份恢复演练 | 🔲 CI 待确认（workflow: Deploy Acceptance (G4) → stack-integration） |

---

### 条目 12：全部通过后才标记基座完成

| 子项 | 证据 | 状态 |
| --- | --- | --- |
| 本地验收 | 上述全部本地可验证项为绿 | ✅ 本地通过 |
| CI Docker 验收 | 需在 GitHub Actions 确认 Deploy Acceptance (G4) 工作流全绿 | 🔲 CI 待确认 |
| 用户确认 | 用户审阅证据清单与 CI 结果后确认 | ⏳ 手动确认 |

---

## CI 工作流映射

| 工作流 | 覆盖条目 | 确认状态 |
| --- | --- | --- |
| CI (`ci.yml`) | 条目 1（G1-G3 全量验收 + 覆盖率 + pip-audit） | 🔲 待合并到 main 后在 Actions 页面确认 |
| Deploy Acceptance (G4) (`deploy-acceptance.yml`) → generate | 条目 3（Copier 生成） | 🔲 待确认 |
| Deploy Acceptance (G4) → g4-local | 条目 2（g4 本地子集 + backup integration） | 🔲 待确认 |
| Deploy Acceptance (G4) → compose-validate | 条目 4（compose config --quiet） | 🔲 待确认 |
| Deploy Acceptance (G4) → image-checks | 条目 4/6（镜像构建 + nginx -t + 非 root + 无密钥） | 🔲 待确认 |
| Deploy Acceptance (G4) → stack-integration | 条目 5/6/7/8/9/10/11（全栈集成测试 + 备份恢复演练 + RPO/RTO 报告） | 🔲 待确认 |

---

## 环境限制说明

当前开发环境（Windows 11）无 Docker。以下验收条目依赖 Docker：
- `docker compose config --quiet`
- 应用镜像构建
- 容器内 `nginx -t`
- 全栈启动与集成测试
- 优雅关闭集成测试
- 备份恢复演练（需 pg_dump/pg_restore + 隔离库）

这些条目在本地以静态断言测试覆盖（验证文件结构与指令），实际容器级验证由 GitHub Actions
工作流 `deploy-acceptance.yml` 承载。用户需在合并到 main 后在 Actions 页面确认该工作流全绿。
