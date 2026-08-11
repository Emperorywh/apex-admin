# 架构检查报告 — SPEC 35

> 逐条核对 SPEC 第 35 章「每次变更后的架构检查」全部 11 项检查项，
> 覆盖全部已注册模块与公共核心。对应 TASK-036 验收条件 4。

执行日期：2026-08-12
执行方式：人工审查代码库 + 自动化契约校验（lint-imports、modules validate、ruff、mypy strict）
SPEC 版本：9de44cc2b96c129ee0adeb0be142242a4052a4653a00dc94d44564aa47a58637

---

## 检查范围

### 已注册模块（11 个）

| 模块 | 权限点 | 错误码 | 审计动作 | 公开 Port | 必需依赖 | 可选依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| example | 2 | 2 | 3 | — | — | — |
| audit | 2 | 2 | 2 | AuditPort, LoginLogPort, SecurityLogPort | — | — |
| identity | 2 | 6 | 7 | UserAuthPort | audit | — |
| auth | — | 4 | — | — | audit, identity, rbac | — |
| rbac | 4 | 9 | 8 | UserRbacPort | audit, identity | — |
| org | 4 | 19 | 16 | UserOrgPort | audit, identity | — |
| menu | 3 | 7 | 8 | — | audit, rbac | — |
| sysconfig | 2 | 8 | 4 | — | audit | — |
| dict | 2 | 10 | 10 | ReferenceRegistryPort | audit | — |
| file | 2 | 11 | 3 | FileReferencePort, FileReadPort | audit | — |
| backup | — | — | — | — | — | file |

### 公共核心

`src/app/core/`（配置、日志、异常、错误码注册表、安全基元、API 框架、上下文、指标、模块契约）
`src/app/api/`（应用工厂、安全头中间件、请求体限制、可信代理、指标端点、健康检查）
`src/app/infrastructure/`（引擎工厂、Unit of Work、Alembic 迁移环境）
`src/app/composition/`（模块清单装配、依赖注入）

---

## 检查项 1：是否增加了不必要的模块耦合

**结论：通过**

跨模块依赖全部通过 `ModuleDefinition.required_dependencies` / `optional_dependencies` 显式声明，
由 `modules validate` 在启动时校验一致性。模块间运行时调用通过公开 Application Port 完成：

- audit → 无外部依赖
- identity → audit（审计写入）
- auth → audit（登录/安全日志）、identity（UserAuthPort）、rbac（UserRbacPort）
- rbac → audit（审计写入）、identity（用户存在性校验）
- org → audit（审计写入）、identity（用户存在性校验）
- menu → audit（审计写入）、rbac（UserRbacPort 查询启用角色）
- sysconfig/dict/file → audit（审计写入）
- backup → file（可选依赖，读取 READY 文件清单）

无模块通过直接导入其他模块的 ORM 模型或 Adapter 实现来跨模块操作数据（backup 对 file 的可选依赖通过延迟导入在函数体内查询文件元数据，仅读不写）。

lint-imports 5 项分层契约全部 KEPT。

---

## 检查项 2：是否跨越了既定分层或模块边界

**结论：通过**

SPEC 5.2 定义的调用流在代码中严格执行：

- **Router → Use Case → Port → Adapter**：路由层不导入 AsyncSession 或 Repository（有 AST 架构测试 `test_router_no_db_import` 验证）。
- **API 层不依赖 Infrastructure 层**：由 lint-imports forbidden 契约强制。
- **Domain 层不反向依赖任何外层**：由 lint-imports forbidden 契约强制。
- **Application 层不依赖 API/Infrastructure/Composition**：由 lint-imports forbidden 契约强制。
- **Composition Root 是唯一同时引用接口与实现的装配位置**：模块清单 `MODULE_MANIFEST` 显式列出全部已启用模块。

模块边界由 `modules validate` 校验：模块编码、权限点编码、错误码、事件编码全局唯一无冲突。

---

## 检查项 3：是否引入了隐式状态

**结论：通过**

- **显式状态管理**：UseCaseContext（frozen dataclass）携带 request_id/actor_id/session_id/current_time/security_metadata，
  每请求通过依赖注入显式传递，不使用线程局部变量或隐式上下文。
- **数据库状态**：通过 Unit of Work 显式管理事务边界，提交/回滚/释放显式调用。
- **文件状态机**：PENDING → READY → DELETING → DELETED / FAILED，状态转换由纯函数 `transition()` 校验，不可非法转换。
- **配置状态**：Fernet 加密的敏感配置通过 `ConfigReadService` 声明式键白名单注入，不依赖隐式全局缓存。
- **RBAC 权限**：基于数据库当前关系授权，无 TTL 缓存，无进程内权限缓存。
- **Token 密钥轮换**：通过 `rotation_expires_at` 时间窗口显式控制，无隐式 fallback。

---

## 检查项 4：是否复制了已有业务规则

**结论：通过**

- 循环检测逻辑（部门 parent_id、菜单 parent_id）提取为公共纯函数 `detect_cycles`（`src/app/core/data_check.py`），
  白/黑两色 DFS 沿 parent 链遍历，被 data check 命令复用。
- 审计变更差异字段白名单 `FieldWhitelist` + `generate_diff` 由 audit 模块提供，
  其他模块（identity、org、menu、sysconfig、dict、file）通过 AuditPort 复用，不自行实现差异计算。
- 分页框架（PageParams/PageResponse/total_pages）、排序框架（parse_sort/sort_dependency）由 `core/api` 提供，
  全部模块复用，不复制分页/排序逻辑。
- 密码哈希、Token 生成、摘要计算由 `core/security` 提供单一实现。

---

## 检查项 5：是否引入了临时 patch、fallback 或兼容逻辑

**结论：通过**

代码库不含以下模式：
- 无 `# TODO: remove`、`# FIXME: temporary`、`# HACK` 标记。
- 无 `try/except` 吞没异常后返回默认值的 fallback 模式（异常要么翻译为领域异常抛出，要么记录后重新抛出）。
- 无 `legacy`、`deprecated`、`backward_compat`、`migration_helper` 命名的函数或模块。
- 无双写逻辑（Token 密钥轮换是设计内的双密钥短期切换，不是兼容层——旧密钥有显式过期窗口，超窗后仅新密钥生效）。
- 无旧接口、旧字段、旧数据兼容代码（SPEC 3: "不保留 legacy、deprecated、灰度或 fallback 逻辑"）。

---

## 检查项 6：是否产生了无法说明用途的抽象

**结论：通过**

全部抽象有明确的 SPEC 引用和用途说明：

- `Port`（ABC）：每个 Port 类的 docstring 引用对应 SPEC 章节并说明公开原因。
- `ModuleDefinition`：显式装配清单，引用 SPEC 5.5。
- `UseCaseContext`：显式请求上下文，引用 SPEC 6.2。
- `Clock` / `IdGenerator`：显式时间/ID 生成的 Port，引用 SPEC 6.2。
- `TransactionalEventDispatcher`：事务内事件分发框架，引用 SPEC 5.7。
- `Initializer` / `InitializationRunner`：幂等初始化框架，引用 SPEC 25.3。
- `FieldWhitelist`：审计差异字段白名单，引用 SPEC 18.2。
- `ConfigEncryptionService`：敏感配置加密，引用 SPEC 16.1 / 23.2。
- `FileStateMachine` / `transition()`：文件状态转换，引用 SPEC 19.3。

无未使用的接口、空实现或预言性抽象。

---

## 检查项 7：是否降低了模块的可测试性

**结论：通过**

- 全部 Port 为 ABC，可在测试中替换为内存/模拟实现。
- Use Case 通过构造函数注入 Port，不依赖全局状态。
- 测试分级清晰：unit（纯逻辑/静态分析）、integration（真实数据库）、api（TestClient 端到端）、security（安全属性验证）、deployment（部署文件断言）。
- 1283 个 G1-G3 测试 + 199 个 G4 本地子集测试全部通过。
- 测试不依赖测试间执行顺序（每个集成测试自行清理数据）。
- `conftest.py` 提供 Testcontainers 数据库 fixture 和本地 PostgreSQL 二进制 fixture，两种模式自动切换。

---

## 检查项 8：是否破坏事务一致性或权限边界

**结论：通过**

- **事务一致性**：
  - Unit of Work 管理单一 AsyncSession，提交/回滚/释放显式调用。
  - 事务内事件在提交前同步执行（`TransactionalEventDispatcher`），失败回滚（有集成测试验证）。
  - 审计写入与业务变更在同一事务内（有集成测试验证同提交/同回滚）。
  - 文件上传使用双事务模式（元数据 PENDING 事务 1 → 原子 rename → READY 事务 2），中间失败由 `files reconcile` 幂等恢复。
- **权限边界**：
  - 全部管理接口声明权限点（架构测试 `test_all_management_routes_declare_permission` 验证 21 条管理路由全部声明）。
  - 自助端点仅需认证，精确豁免（`/users/me`、`/auth/logout` 等）。
  - 越权测试覆盖普通管理员不能提升自身权限、操作超出范围用户、破坏最后一个超级管理员。
  - 文件下载仅允许上传者或管理员，跨用户下载被拒绝。
  - 私有文件不能绕过应用授权直接下载（Nginx 无 root/alias 静态直出）。

---

## 检查项 9：是否使数据流和状态流更难推导

**结论：通过**

- **调用流单向**：HTTP → Router → Use Case → Domain Policy → Port → Adapter → Database。
  每层有清晰命名（router.py / use_case.py / models.py / port.py / adapter.py / orm.py）。
- **事件流显式**：事件编码在 ModuleDefinition 中声明，处理器在定义中注册，
  事件流可从模块定义直接推导（identity 产生 USER.DISABLED → auth/org 处理器监听）。
- **状态流显式**：文件状态机转换规则在 `state_machine.py` 中以函数 `transition()` 纯表达，
  不可非法转换。用户状态（active/disabled）由枚举管理。
- **配置流显式**：配置通过 pydantic-settings 从环境变量加载，生产环境启动校验拒绝缺失/弱密钥。
- **审计流显式**：每个业务变更的审计写入通过 AuditPort 同事务提交，审计动作编码在模块定义中声明。

---

## 检查项 10：是否增加了部署和运维复杂度

**结论：通过**

- **单机部署**：Docker Compose 编排 PostgreSQL + Nginx + 2 API Worker + 一次性 migrate 服务。
  全部镜像使用固定摘要（SHA-256），服务重启策略与健康检查齐全。
- **迁移门禁**：migrate 服务 `service_completed_successfully` 门禁 + API `/health/ready` revision 检查双重保险。
- **停机切换**：文档化的停旧版 → 迁移 → 启新版顺序（docs/deployment-guide.md）。
- **备份恢复**：CLI `backup create`（pg_dump + 文件清单）/ `backup verify`（隔离库恢复 + 结构化报告）。
  备份保留策略可配置（日 7 / 周 4 滚动）。
- **反向代理**：唯一受支持 Nginx 配置，29 项静态 lint 校验，HTTPS/限流/超时/安全头全覆盖。
- **无额外组件**：不强制依赖 Redis、消息队列、对象存储、Kubernetes。
- **运维命令**：CLI 提供全部管理命令（db check/upgrade、modules validate、auth create-admin/sync-permissions/rotate-token-keys、admin sync-seeds、data check、files reconcile、audit cleanup、backup create/verify、sysconfig re-encrypt）。

---

## 检查项 11：是否降低了开发者和 AI 的可维护性

**结论：通过**

- **命名清晰**：模块/文件/类/函数命名一致遵循分层约定（Router/UseCase/Port/Adapter/ORM/Domain Model）。
- **模块契约显式**：每个模块的 `definition.py` 是单一事实来源，声明权限点、错误码、审计动作、事件、Port、依赖。
- **文档完备**：
  - `docs/development.md` — 本地开发指南（SPEC 30.1）
  - `docs/module-development-guide.md` — 模块开发规范（SPEC 30.2）
  - `docs/deployment-guide.md` — 部署指南
  - `docs/backup-recovery.md` — 备份恢复
  - `docs/versioning-policy.md` — 版本策略
  - 4 份 ADR — 技术栈、分层架构、PostgreSQL 供应、uv.lock/Copier 策略
- **示例模块**：`example` 模块端到端演示 Router → Use Case → Port → Adapter → 迁移 → 权限码 → 错误码 → 审计 → 事件 → 测试的完整接入。
- **类型安全**：mypy --strict 全量通过，全部 Port/UseCase/Schema 有完整类型标注。
- **测试可读**：测试名描述场景和预期，测试分级标记（g1-g4 / unit-integration-api-security-deployment）。
- **CI 自动化**：lint + test + coverage + pip-audit 全自动，部署验收工作流覆盖 Docker 依赖条目。

---

## 总结

| 检查项 | 结论 |
| --- | --- |
| 1. 不必要的模块耦合 | 通过 |
| 2. 跨越分层或模块边界 | 通过 |
| 3. 隐式状态 | 通过 |
| 4. 复制已有业务规则 | 通过 |
| 5. 临时 patch/fallback/兼容逻辑 | 通过 |
| 6. 无法说明用途的抽象 | 通过 |
| 7. 可测试性 | 通过 |
| 8. 事务一致性或权限边界 | 通过 |
| 9. 数据流和状态流可推导性 | 通过 |
| 10. 部署和运维复杂度 | 通过 |
| 11. 开发者和 AI 可维护性 | 通过 |

全部 11 项检查通过。代码库无架构阻塞问题。
