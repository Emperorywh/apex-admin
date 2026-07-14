# Apex Admin API

NestJS 11 + Prisma 7.8 + PostgreSQL 的单组织后台 API。当前实现以 [`SPEC_0001_auth-rbac.md`](docs/SPEC_0001_auth-rbac.md) 为准，提供账号开通、双 Token 会话、refresh 轮换/重放检测、固定角色 RBAC、对象级授权和最小安全审计。

## 架构

- `src/platform`：强类型配置、数据库生命周期、HTTP 基础能力和健康检查。
- `src/modules/iam/domain`：纯 TypeScript 值对象、聚合、状态机和授权事实来源。
- `src/modules/iam/application`：一个业务动作一个 Use Case，依赖抽象 Port 与显式 IAM UoW。
- `src/modules/iam/infrastructure`：Prisma、Argon2id、JWT、随机 token 和单副本限流 Adapter。
- `src/modules/iam/presentation`：DTO、Controller、Guard、Cookie 工厂与 Problem Details。

模块数据流和状态归属见 [`src/modules/iam/README.md`](src/modules/iam/README.md)，HTTP 契约见 [`docs/openapi.yaml`](docs/openapi.yaml)。

## 本地配置

复制 [`.env.example`](.env.example) 为 `.env`，替换所有示例 Secret。关键边界：

- Runtime 只读取 `DATABASE_URL`、JWT、HTTP、Cookie、Argon2 与限流配置。
- Prisma CLI 只读取 `MIGRATION_DATABASE_URL`，不回退到 Runtime 连接串。
- `seed:super-admin` 只读取 `DATABASE_URL`、`SUPER_ADMIN_EMAIL`、`SUPER_ADMIN_PASSWORD` 和 Argon2 参数。
- `JWT_ACCESS_SECRET_BASE64` 必须是至少 32 随机字节的严格 base64 编码。
- 生产环境强制 `COOKIE_SECURE=true`，`CORS_ORIGINS` 必须是精确白名单。

## 安装与启动

```bash
pnpm install
pnpm prisma:generate
pnpm prisma:migrate:deploy
pnpm seed:super-admin
pnpm build
pnpm start:prod
```

生产发布必须使用不同的 Migration、Bootstrap、Runtime 数据库角色，并按 [`scripts/database-role-grants.sql`](scripts/database-role-grants.sql) 收敛权限。运行与换钥步骤见 [`docs/runbooks/iam-operations.md`](docs/runbooks/iam-operations.md)。

## 质量门禁

```bash
pnpm typecheck
pnpm lint:check
pnpm test:unit
pnpm test:architecture
pnpm test:integration
pnpm test:e2e
pnpm build
```

`test:integration` 与 `test:e2e` 使用 Testcontainers 启动真实 PostgreSQL 17，因此需要可用的 Docker/兼容容器运行时；不存在 SQLite 或远程数据库 fallback。`lint:check` 只读，不修改文件；需要显式修复时才运行 `lint:fix`。

健康检查：

- `GET /v1/health/live`
- `GET /v1/health/ready`

本 MVP 的内存登录限流只允许单应用副本。扩容前必须替换为独立规格定义的 Redis 原子实现，不保留动态双实现 fallback。
