# 变更日志

本项目遵循 [语义化版本](https://semver.org/)。版本类型定义见 [版本策略](docs/versioning-policy.md)。

## [Unreleased] — v0.1.0（初始基座）

### G1 Core Ready

- 模块化单体架构与分层依赖契约（composition → api → application → domain）
- pydantic-settings 配置体系（APEX_ 前缀，生产环境安全校验）
- FastAPI Lifespan 生命周期（启动/关闭钩子）
- structlog 结构化日志（dev 彩色 / prod JSON，敏感字段递归掩码）
- Request ID 中间件（自动生成 + 回写响应头 + 日志关联）
- SQLAlchemy 2.0 异步引擎 + Unit of Work（pool_size=5, max_overflow=5）
- Alembic 异步迁移（全局单头 revision 图，模块化 version_locations）
- RFC 9457 problem+json 异常处理器（统一错误响应格式）
- 错误码注册表（全局唯一、格式校验、元数据分离）
- API 通用规范（分页框架、排序框架、StrictBaseModel、OpenAPI 定制）
- 模块接入契约（ModuleDefinition + 启动校验：重复/依赖缺失/循环依赖）
- 事务内事件分发框架（提交前同步执行、失败回滚、稳定排序）
- 幂等初始化框架（Initializer + InitializationRunner）
- 健康检查端点（/health/live + /health/ready）
- 最小示例模块（example：完整 Router → Use Case → Port → Adapter → 迁移 → 权限码 → 错误码 → 审计 → 事件 → 测试）

### G2 Security Ready

- Argon2id 密码哈希（memory_cost=65536, time_cost=3, parallelism=1）
- CSPRNG Token 生成器（secrets.token_urlsafe(32)，256 bit 熵）
- HMAC-SHA-256 双密钥摘要服务（启动校验密钥安全性）
- 用户管理（CRUD / 启用禁用 / 重置密码 / 物理删除保护 / 自助查询与更新）
- 不透明 Access Token + Refresh Token 轮换 + 在线会话校验
- 登录安全（防枚举虚拟哈希、双维度失败限制 5/20 次×15 分钟、rehash 升级）
- Cookie 安全（Secure / HttpOnly / SameSite=Strict / Path=/ / 无 Domain）
- RBAC 角色与权限点（内置角色保护、权限点目录同步、基于数据库当前关系授权）
- 超级管理员保护（不可删除最后一个、不可自降权限）
- 操作审计与登录日志（同事务写入、不可变、变更差异字段白名单、显示名快照）
- 管理命令（auth create-admin / auth sync-permissions / auth rotate-token-keys）

### G3 Admin Ready

- 部门管理（树形 CRUD / 循环防护三重覆盖：直接+递归 CTE+事务咨询锁 / 删除保护）
- 岗位与用户组织关系（岗位 CRUD / 用户分配 / 主部门 / 离职清理）
- 菜单管理（树形 CRUD / 角色菜单分配 / 当前用户菜单树与权限编码 / 可见性≠授权）
- 系统配置（类型校验 / Fernet 加密 / 敏感值掩码 / 核心安全保护 / 声明式键白名单读取）
- 数据字典（CRUD / 稳定值约定 / 跨模块引用登记与删除保护）
- 文件管理（UUID 安全命名 / 防目录穿越 / magic bytes 校验 / 流式写入 + SHA-256 / 状态机 / 原子 rename / 跨用户授权）
- 文件一致性命令（files reconcile --dry-run/--apply，确定性规则，幂等恢复）
- 管理命令与数据检查（admin sync-seeds / data check / audit cleanup / sysconfig re-encrypt）

### G4 Production Ready

- HTTP 安全基线（安全头 / 请求体限制 / 可信代理 / CORS / Host 白名单生产校验）
- 运行指标（prometheus-client：请求计数 / 耗时直方图 / DB 连接池 Gauge / 慢查询识别）
- 容器化（多阶段 Dockerfile / 固定摘要基础镜像 / 非 root / .dockerignore）
- Docker Compose 编排（PostgreSQL 18 + Nginx + 2 API Worker + 一次性 migrate 门禁）
- Nginx 反向代理（HTTPS 终结 / 代理头 / 上传限制 / 登录与刷新限流 / 超时 / /metrics 排除）
- 备份与恢复（pg_dump 逻辑全量 + READY 文件清单 + 滚动保留 + 隔离库恢复演练 + RPO/RTO 报告）
- Copier 模板分发（身份替换 / uv.lock 协同 / 生成验证线束）
- CI 工作流（G1-G3 验收 + 覆盖率门禁 + pip-audit / G4 部署验收 + Docker 全栈集成测试）

### 文档

- 本地开发指南（SPEC 30.1）
- 模块开发规范（SPEC 30.2）
- 部署指南与反向代理配置（SPEC 26.1-26.3 / 33.4）
- 备份恢复与调度（SPEC 27.1-27.3）
- 版本策略（SPEC 30.3）
- 4 份架构决策记录（ADR）
- 组织关系处理规则、字典稳定值约定

### 基座完成状态

> ⚠️ 本版本标记为 **v0.1.0 候选**。本地全量验收已通过（G1-G3 测试 + G4 本地子集 + 静态分析全绿），
> 但 Docker 依赖的 G4 验收条目需在 GitHub Actions 中确认。用户审阅 CI 结果并确认后，
> 方可正式标记为"基座完成"并打 Git Tag `v0.1.0`。
