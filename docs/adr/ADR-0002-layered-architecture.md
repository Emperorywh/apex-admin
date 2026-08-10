# ADR-0002：分层架构与依赖方向

- **状态**：accepted
- **日期**：2026-08-10

## 背景

基座采用模块化单体架构，需要清晰的分层来保证领域逻辑的可测试性和
可维护性。SPEC 5.2 定义了编译期代码依赖方向，需要在项目启动时确立分层
边界并设置自动化约束机制。

## 决策

采用四层分层架构，编译期依赖方向如下：

```text
API ───────────────→ Application ───────────────→ Domain
Infrastructure ────→ Application / Domain 定义的 Port
Composition Root ──→ API + Application + Infrastructure
```

各层职责：

- **API 层**：Router、请求/响应 Schema。依赖 Application 层。
- **Application 层**：Use Case、事务边界控制。依赖 Domain 层。
- **Domain 层**：领域策略、领域服务、Port 定义。不依赖任何外层。
- **Infrastructure 层**：实现 Application/Domain 定义的 Port（Repository、
  文件存储、外部服务适配器）。不暴露 SQLAlchemy、FastAPI 等具体框架类型
  到内层。
- **Composition Root**：唯一允许同时引用接口与具体实现并执行装配的位置。

通过 import-linter 在 CI 中强制执行上述契约（SPEC 5.2、29.1）。

## 理由

1. **依赖方向单向**：外层依赖内层，内层不依赖外层实现，保证领域逻辑
   可独立测试，不受框架变更影响。
2. **Port 与 Adapter 分离**：Infrastructure 只实现内层定义的抽象，使得
   数据库、外部服务等可在测试中替换，集成测试使用真实 PostgreSQL 而非
   mock（SPEC 5.4 禁止用 SQLite 替代）。
3. **Composition Root 显式装配**：禁止包扫描和导入副作用自动发现模块
   （SPEC 5.5），所有模块必须在装配根显式注册。
4. **import-linter 自动约束**：编译期依赖方向不能仅靠文档约定，需要
   自动化工具在 CI 中持续验证，防止长期维护中架构腐化。

## 影响

- 新增模块必须遵循分层依赖方向，违反方向的 import 会被 CI 拦截。
- 领域层不得依赖 FastAPI、ORM、HTTP 或具体存储 SDK。
- 事务边界由最外层写 Use Case 控制，Router 和 Repository 不得提交事务。
- DTO、API Schema、领域对象和 ORM 模型职责分离。
