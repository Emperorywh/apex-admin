# 文档完备性核对表 — SPEC 30.1 / 30.2 / 33.4

> 逐条核对基座交付的全部文档是否覆盖对应 SPEC 要求。
> 对应 TASK-036 验收条件 3。

核对日期：2026-08-12

---

## 30.1 本地开发（G1）

SPEC 30.1 要求提供：本地开发启动说明、环境变量示例、数据库准备和迁移说明、运行测试说明、常见问题排查说明。

| 要求 | 文档位置 | 覆盖状态 |
| --- | --- | --- |
| 本地开发启动说明 | `docs/development.md` §2 本地启动（配置环境变量、启动数据库两种方式、启动开发服务器） | ✅ |
| 环境变量示例 | `.env.example`（全部 APEX_ 前缀配置项含注释）；README §环境变量表格 | ✅ |
| 数据库准备和迁移说明 | `docs/development.md` §3 数据库准备（Docker / 本地二进制 / 迁移命令） | ✅ |
| 运行测试说明 | `README.md` §运行测试；`docs/development.md` §4 运行测试（门槛标记、覆盖率） | ✅ |
| 常见问题排查说明 | `docs/development.md` §常见问题 | ✅ |

**结论：齐全。**

---

## 30.2 新业务模块开发规范（G1）

SPEC 30.2 要求提供 11 项模块开发文档和一个最小示例模块。

| 要求 | 文档位置 | 覆盖状态 |
| --- | --- | --- |
| 模块目录模板 | `docs/module-development-guide.md` §1 模块目录结构 | ✅ |
| 如何注册路由 | `docs/module-development-guide.md` §2 路由注册 | ✅ |
| 请求和响应 Schema | `docs/module-development-guide.md` §3 请求与响应 Schema | ✅ |
| 应用服务和事务 | `docs/module-development-guide.md` §4 应用服务与事务 | ✅ |
| 定义权限点 | `docs/module-development-guide.md` §5 权限点 | ✅ |
| 注册错误码 | `docs/module-development-guide.md` §6 错误码 | ✅ |
| 记录审计日志 | `docs/module-development-guide.md` §7 审计日志 | ✅ |
| 单元测试和集成测试 | `docs/module-development-guide.md` §8 测试 | ✅ |
| 模块间允许的依赖方式 | `docs/module-development-guide.md` §9 跨模块依赖 | ✅ |
| 最小示例模块（完整接入） | `src/app/modules/example/`（Router → Use Case → Port → Adapter → 迁移 → 权限码 → 错误码 → 审计 → 事件 → 测试） | ✅ |
| 示例模块无演示数据 | 示例模块迁移创建空表，无预置业务记录；Copier 生成项目保留示例模块 | ✅ |

**结论：齐全。**

---

## 部署文档（SPEC 33.4 / 26.1-26.3）

| 要求 | 文档位置 | 覆盖状态 |
| --- | --- | --- |
| 单机生产部署方案 | `docs/deployment-guide.md`（停机切换顺序、迁移门禁、回滚步骤、Docker 构建验证） | ✅ |
| 反向代理配置 | `docs/nginx-proxy-config.md`（HTTPS 终结、代理头、限流、超时、/metrics 排除、私有文件授权边界） | ✅ |
| 容器化方案 | `Dockerfile`（多阶段构建）+ `deploy/compose.yaml`（PostgreSQL + Nginx + 2 API Worker + migrate）+ `.dockerignore` | ✅ |
| 连接预算 | `docs/connection-budget.md`（26.1 公式 2×(5+5)=20 ≤ 100-预留，扩展计算） | ✅ |
| HTTPS/代理约定 | `docs/deployment-conventions.md`（HTTPS 终结约定、代理头信任边界、/metrics 内网访问控制） | ✅ |
| CI 部署验证 | `docs/ci-deployment-verification.md`（用户手动验证指引） | ✅ |

**结论：齐全。**

---

## 备份恢复文档（SPEC 27.1-27.3 / 33.4）

| 要求 | 文档位置 | 覆盖状态 |
| --- | --- | --- |
| 数据库恢复 | `docs/backup-recovery.md` §数据库恢复 | ✅ |
| 文件恢复 | `docs/backup-recovery.md` §文件恢复 | ✅ |
| 新服务器完整恢复 | `docs/backup-recovery.md` §新服务器完整恢复（三步骤） | ✅ |
| 演练说明 | `docs/backup-recovery.md` §恢复演练（明确演练不覆盖生产） | ✅ |
| 备份调度 | `docs/backup-scheduling.md`（Linux cron + Windows 任务计划、每日备份与每月演练调度、失败告警、异盘存储） | ✅ |

**结论：齐全。**

---

## 密钥轮换文档（SPEC 12.2 / 17.2 / 23.2）

| 要求 | 文档位置 | 覆盖状态 |
| --- | --- | --- |
| Token HMAC 密钥轮换 | `docs/development.md` §管理命令（`auth rotate-token-keys` 双密钥短期切换步骤）；`src/app/core/security/digest.py`（`rotation_expires_at` 窗口控制） | ✅ |
| 敏感配置加密密钥轮换 | `src/app/modules/sysconfig/definition.py`（`sysconfig re-encrypt` 命令声明）；`src/app/modules/sysconfig/encryption.py`（Fernet 双密钥短期切换） | ✅ |
| 密钥安全校验 | `src/app/core/config.py`（生产环境密钥缺失/相同/长度不足 32 字节/默认弱密钥启动失败校验） | ✅ |
| 生产环境初始化流程 | `docs/development.md` §生产环境部署流程（步骤 5: 可选 `auth rotate-token-keys`） | ✅ |

**结论：齐全。**

---

## 版本策略文档（SPEC 30.3）

| 要求 | 文档位置 | 覆盖状态 |
| --- | --- | --- |
| Copier 模板初始化 | `docs/versioning-policy.md` §1（固定使用 Copier、身份问题、生成后产物、无演示数据、EXT 开关） | ✅ |
| 语义化版本与 Git Tag | `docs/versioning-policy.md` §2（Patch/Minor/Major 语义、Git Tag 发布流程、安全修复发布） | ✅ |
| 派生项目更新 | `docs/versioning-policy.md` §3（copier update、差异人工评审、Major 版本升级、回答保留） | ✅ |
| uv.lock 协同策略 | `docs/versioning-policy.md` §4 + `docs/adr/ADR-0004-uv-lock-copier-strategy.md` | ✅ |
| 验证线束 | `docs/versioning-policy.md` §5（verify_generated.py 单一有界入口） | ✅ |
| 模板版本号与 SPEC 对应 | `docs/versioning-policy.md` §6（0.1.x → G1-G4 初始基座） | ✅ |

**结论：齐全。**

---

## 架构决策记录（ADR）

| 文档 | 主题 | 覆盖状态 |
| --- | --- | --- |
| `docs/adr/README.md` | ADR 流程与模板 | ✅ |
| `docs/adr/ADR-0001-technology-stack.md` | 技术栈基线（SPEC 5.4） | ✅ |
| `docs/adr/ADR-0002-layered-architecture.md` | 分层架构与依赖方向（SPEC 5.2） | ✅ |
| `docs/adr/ADR-0003-local-postgresql-supply.md` | 本地 PostgreSQL 供应策略 | ✅ |
| `docs/adr/ADR-0004-uv-lock-copier-strategy.md` | uv.lock 与 Copier 模板策略 | ✅ |

**结论：齐全。**

---

## 其他约定文档

| 文档 | 主题 | 覆盖状态 |
| --- | --- | --- |
| `docs/org-relation-rules.md` | 用户组织关系处理规则（离职/禁用） | ✅ |
| `docs/dict-stable-value-convention.md` | 字典稳定值约定（业务持久化） | ✅ |

---

## 总结

| 文档类别 | 条目数 | 齐全 |
| --- | --- | --- |
| 30.1 本地开发 | 5 | ✅ |
| 30.2 模块开发规范 | 11 | ✅ |
| 部署文档 | 6 | ✅ |
| 备份恢复文档 | 5 | ✅ |
| 密钥轮换文档 | 4 | ✅ |
| 版本策略文档 | 6 | ✅ |
| ADR | 5 | ✅ |
| 其他约定 | 2 | ✅ |

**全部文档核对表无缺项。**
