# SPEC-0001：注册、登录与 RBAC 权限控制

> 状态：已定稿（基于 4 轮需求访谈）  
> 日期：2026-07-14  
> 适用工程：`api`（NestJS 11 + Prisma 7.8 + PostgreSQL）  
> 架构基线：遵循 [`NestJS-Prisma-PostgreSQL-最佳实践.md`](./NestJS-Prisma-PostgreSQL-最佳实践.md)（下文简称「基线文档」）  
> 范围：MVP 核心认证与授权主链路

---

## 0. 决策摘要（访谈结论一览）

| 维度 | 决策 | 关键取舍 |
| --- | --- | --- |
| 系统拓扑 | **单组织内部后台** | 无 `tenant_id`；`email` 全局唯一 |
| 账号开通 | **仅管理员后台创建** | 无公开注册端点；需 bootstrap 路径创建首个超管 |
| 交付范围 | **MVP 核心** | 注册/登录/登出/刷新 + RBAC；暂不含密码重置、邮箱验证、锁定审计、MFA |
| 令牌传输 | **双 Token** | access JWT（body/Bearer 头）+ refresh（httpOnly Cookie），轮换+撤销+重放检测 |
| RBAC 模型 | **扁平固定角色（enum）** | `SUPER_ADMIN/ADMIN/OPERATOR/VIEWER`；权限码硬编码映射 |
| 权限解析 | **编入 access token** | 0 次查库；收权 ≤ access TTL（15min）生效 |
| 授权层级 | **路由级 + 对象级** | `@RequirePermissions` + `UserPolicy.can(actor, action, target)` |
| 超管保护 | **seed 超管 + 领域不变量** | 扁平 enum 下以领域规则替代 `isSystem` 列（见 §7.3） |
| 密码策略 | **基础（Argon2id + 长度）** | 长度 12–128；不强制复杂度；不接 HIBP |
| 登录限流 | **纳入 MVP：IP + 账号双维度** | 60s 窗口；per-IP ≤10、per-email ≤5；超限 429 |
| Refresh 模型 | **多设备 + 家族重放检测** | 旧 token 被重用 → 吊销整个家族（疑似被盗） |
| 错误语义 | **实用优先：精确错误** | 接受内部后台的枚举风险（见 §11.4 已接受风险） |
| 架构保真度 | **完整 DDD 分层（文档合规）** | 值对象 / Port / Adapter / Mapper / UnitOfWork |
| 首个超管 | **env 驱动 seed** | `SUPER_ADMIN_EMAIL/PASSWORD`，部署 Job 幂等创建 |
| 配置与密钥 | **zod 启动校验 + 单静态 JWT 密钥** | 不启用 kid 轮换 |
| 测试深度 | **集成 + E2E 为主** | 真实 PG（Testcontainers）；扁平 enum 省略纯 domain 单测 |

---

## 1. 范围

### 1.1 本期交付（In Scope）

- 管理员创建用户（账号开通）
- 登录、刷新、登出、获取当前用户（`/auth/me`）
- access/refresh 双令牌签发、轮换、撤销、重放检测
- 扁平角色 RBAC：路由级权限守卫 + 对象级 Policy
- 默认拒绝的全局认证 Guard
- 首个 SUPER_ADMIN 的 bootstrap
- Argon2id 密码哈希、登录限流、配置启动校验
- 真实 PostgreSQL 集成测试 + 关键 HTTP E2E

### 1.2 暂不交付（Out of Scope，后续迭代）

- 密码找回/重置（忘密码流程）
- 邮箱验证、邀请链接激活
- 账号锁定（失败次数锁定）、强制首登改密
- MFA / 双因素、step-up 认证
- 完整审计日志（仅保留必要运行日志）
- Transactional Outbox（MVP 无异步副作用，见 §17）
- JWT 密钥 kid 轮换、JWKS
- 多租户、RLS

### 1.3 已接受的遗留风险（见 §11）

- 精确错误带来的用户枚举面（内部后台，可接受）
- 管理员创建账号时设定初始密码，需带外告知用户（无强制改密）
- 单静态 JWT 密钥（轮换需重启）
- 单副本内存限流（多副本需引入 Redis）

---

## 2. 系统拓扑假设

- **单组织内部后台**：无租户概念。所有用户共享同一全局账号空间。
- `users.email_normalized` **全局唯一**；自然唯一约束不含租户列。
- 授权判定只需回答：「**是谁（身份） + 什么角色（RBAC） + 能否对该资源执行该动作（Policy）**」，不涉及租户边界。
- 若未来演进为多租户 SaaS，需重新评估 schema（所有唯一约束/索引追加 `tenant_id`）与 RLS——**不在本期承诺平滑迁移**。

---

## 3. 架构与目录结构

遵循基线文档 §3（分层、依赖方向、模块所有权）。完整 DDD 分层。认证特性拆为两个模块，以演示跨模块 `public-api.ts` 边界：

```text
src/
├─ bootstrap/
│  ├─ bootstrap.ts              # 唯一启动入口：helmet/cors/pipe/versioning/shutdown
│  └─ setup-application.ts
├─ platform/
│  ├─ config/                   # @nestjs/config + zod schema（§10）
│  ├─ database/                 # DatabaseModule + DatabaseClient（§8）
│  ├─ logging/                  # 结构化 Logger、请求 ID 中间件
│  ├─ observability/            # 健康检查（live/ready）
│  └─ http/                     # 全局 ValidationPipe、Problem Details 异常过滤器、信封拦截器
├─ modules/
│  ├─ identity/                 # 认证模块，持有 refresh_tokens
│  │  ├─ identity.module.ts
│  │  ├─ public-api.ts          # 对外：JwtAuthGuard、@Public/@RequirePermissions/@CurrentUser、AuthFacade
│  │  ├─ presentation/http/
│  │  │  ├─ auth.controller.ts        # login / refresh / logout / me
│  │  │  ├─ guards/jwt-auth.guard.ts  # 全局默认拒绝
│  │  │  ├─ decorators/               # @Public @RequirePermissions @CurrentUser
│  │  │  └─ dto/
│  │  ├─ application/
│  │  │  ├─ use-cases/                # login / refresh-token / logout / get-current-user
│  │  │  └─ ports/                    # refresh-token.repository, credential-checker, token-hasher
│  │  ├─ domain/
│  │  │  ├─ value-objects/            # TokenHash / FamilyId / RefreshTokenExpiry
│  │  │  ├─ refresh-token.aggregate.ts
│  │  │  └─ errors/
│  │  └─ infrastructure/
│  │     ├─ persistence/prisma/       # prisma-refresh-token.repository + mapper
│  │     ├─ jwt/                      # JwtTokenService（签发/验证）
│  │     ├─ crypto/                   # opaque token 生成 + SHA-256 哈希
│  │     └─ ratelimit/                # 登录限流（内存滑动窗口，Port 化）
│  └─ users/                    # 用户模块，持有 users
│     ├─ users.module.ts
│     ├─ public-api.ts          # 对外：UsersFacade（按邮箱查、按 id 查）
│     ├─ presentation/http/
│     │  ├─ users.controller.ts       # 创建/列表/详情/改角色/启停
│     │  └─ dto/
│     ├─ application/
│     │  ├─ use-cases/                # create-user / list-users / get-user / assign-role / disable-user / enable-user
│     │  ├─ policies/user.policy.ts   # 对象级授权
│     │  └─ ports/                    # user.repository, password-hasher, id-generator, users.unit-of-work
│     ├─ domain/
│     │  ├─ entities/user.aggregate.ts
│     │  ├─ value-objects/            # UserId / Email / PasswordHash / UserRole / UserStatus
│     │  └─ errors/                   # UserEmailAlreadyUsed / LastSuperAdmin / InsufficientPrivilege ...
│     └─ infrastructure/
│        └─ persistence/prisma/       # prisma-user.repository + mapper + prisma-users.unit-of-work
└─ shared/kernel/              # Clock、分页值对象等稳定抽象
```

### 3.1 模块所有权

| 模块 | 拥有的表 | 公开出口 |
| --- | --- | --- |
| `users` | `users` | `UsersFacade`（`findByNormalizedEmail`、`findById`）、`UserPolicy` |
| `identity` | `refresh_tokens` | `JwtAuthGuard`、装饰器、`AuthFacade` |

**跨模块规则**：`identity` 认证时通过 `users` 的 `public-api.ts` 校验凭证，**禁止**直接注入 `users` 的 Repository 或查询 `users` 表。两个模块各自提交事务，互不共享事务（基线文档 §3.4）。

---

## 4. 数据模型（Prisma Schema）

单组织，无 `tenant_id`。沿用 `prisma-client-js` 生成器（理由见 [ADR-0001](./adr/0001-prisma-generator-choice.md)）。

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
}

/// 扁平固定角色。SUPER_ADMIN 为受保护值（见 §7.3）。
enum UserRole {
  SUPER_ADMIN
  ADMIN
  OPERATOR
  VIEWER

  @@map("user_role")
}

/// MVP 仅两种状态；禁用≠删除。
enum UserStatus {
  ACTIVE
  DISABLED

  @@map("user_status")
}

/// 用户聚合根。邮箱全局唯一。
model User {
  id              String     @id @db.Uuid
  email           String     @db.VarChar(320)
  emailNormalized String     @map("email_normalized") @db.VarChar(320)
  passwordHash    String     @map("password_hash")
  role            UserRole
  status          UserStatus @default(ACTIVE)
  version         Int        @default(0)        // 乐观锁令牌
  createdAt       DateTime   @default(now()) @map("created_at") @db.Timestamptz(3)
  updatedAt       DateTime   @updatedAt @map("updated_at") @db.Timestamptz(3)

  refreshTokens   RefreshToken[]

  /// 大小写归一化邮箱全局唯一，是并发下保证唯一的最终防线。
  @@unique([emailNormalized], map: "uq_users_email_normalized")
  @@index([role, status], map: "idx_users_role_status")
  @@map("users")
}

/// Refresh 令牌记录。不透明随机串的哈希；不存原值。
model RefreshToken {
  id           String      @id @db.Uuid
  userId       String      @map("user_id") @db.Uuid
  familyId     String      @map("family_id") @db.Uuid
  tokenHash    String      @map("token_hash")
  /// 该 token 被轮换后指向新 token 的 id；用于区分「轮换后被重用（疑似被盗）」与「登出注销」。
  /// 故意不建外键，仅作重放检测的元数据。
  replacedById String?     @map("replaced_by_id") @db.Uuid
  expiresAt    DateTime    @map("expires_at") @db.Timestamptz(3)
  /// 非 null 表示已失效（轮换或登出/吊销）。
  revokedAt    DateTime?   @map("revoked_at") @db.Timestamptz(3)
  createdAt    DateTime    @default(now()) @map("created_at") @db.Timestamptz(3)

  user         User        @relation(fields: [userId], references: [id], onDelete: Cascade)

  @@unique([tokenHash], map: "uq_refresh_tokens_hash")
  @@index([userId, familyId, revokedAt], map: "idx_refresh_tokens_family")
  @@index([expiresAt], map: "idx_refresh_tokens_expires")
  @@map("refresh_tokens")
}
```

### 4.1 自定义迁移 SQL（CHECK 与清理）

Prisma 无法表达「最后一个超管」这类跨行不变量，交给应用层（§7.3）+ 数据库约束补充：

```sql
-- 邮箱归一化格式兜底（应用层 Email 值对象为主，此为最终防线）
ALTER TABLE users
  ADD CONSTRAINT ck_users_email_format
  CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' AND length(email) <= 320);

-- refresh_tokens 行级一致性
ALTER TABLE refresh_tokens
  ADD CONSTRAINT ck_refresh_tokens_revoke_consistency
  CHECK (
    (revoked_at IS NULL AND replaced_by_id IS NULL)
    OR revoked_at IS NOT NULL
  );
```

> 说明：`replaced_by_id` 仅当 `revoked_at` 非空时才有意义（轮换时同时置 `revoked_at` 与 `replaced_by_id`）；登出仅置 `revoked_at`。

### 4.2 过期清理

- 定时任务（**后续迭代**，MVP 仅靠 `expiresAt` 过期判定）扫描 `expiresAt < now` 删除过期 token。
- MVP 在刷新校验时直接判定 `expiresAt`，过期即当作无效。

---

## 5. 领域模型（`users` 模块）

### 5.1 值对象

| 值对象 | 不变量 |
| --- | --- |
| `UserId` | UUIDv7；由注入的 `IdGenerator` 生成；不可空 |
| `Email` | RFC 简单格式 + 长度 ≤320；构造时产生 `normalizedValue`（小写 + trim）；`restore()` 用于读库 |
| `PasswordHash` | Argon2id 输出字符串；`restore()` 仅从持久层重建，不暴露明文 |
| `UserRole` | 枚举值之一；`SUPER_ADMIN` 受保护 |
| `UserStatus` | `ACTIVE` / `DISABLED`；状态迁移由聚合方法控制 |

### 5.2 `User` 聚合

```ts
// 概念示意，非最终实现
class User {
  private constructor(/* … */) {}

  static create(input: { id; email; passwordHash; role }): User {
    // 不变量校验；初始 status = ACTIVE
  }

  static restore(/* 持久层快照 */): User { /* 读库重建 */ }

  assignRole(actorRole: UserRole, next: UserRole): void {
    // 授权规则在 UserPolicy；聚合只负责状态迁移合法性
    this.role = next;
  }

  disable(): void { this.status = UserStatus.DISABLED; }
  enable(): void { this.status = UserStatus.ACTIVE; }
  // pullDomainEvents()：MVP 暂无领域事件（无 Outbox）
}
```

### 5.3 领域错误（不抛 NestJS 异常，由全局过滤器映射 HTTP）

| 领域错误 | HTTP 映射 | 稳定错误码 |
| --- | --- | --- |
| `UserEmailAlreadyUsedError` | 409 | `USER_EMAIL_ALREADY_USED` |
| `UserNotFoundError` | 404 | `USER_NOT_FOUND` |
| `InvalidCredentialsError` | 401 | `INVALID_CREDENTIALS` |
| `UserDisabledError` | 401 | `USER_DISABLED` |
| `InsufficientPrivilegeError` | 403 | `INSUFFICIENT_PRIVILEGE` |
| `LastSuperAdminError` | 409 | `LAST_SUPER_ADMIN` |
| `RefreshTokenInvalidError` | 401 | `REFRESH_TOKEN_INVALID` |
| `RefreshTokenReplayError` | 401 | `REFRESH_TOKEN_REPLAY` |
| `RateLimitExceededError` | 429 | `RATE_LIMIT_EXCEEDED` |

---

## 6. 认证流程

### 6.1 登录 `POST /v1/auth/login`

```text
请求 { email, password }
  → ValidationPipe → LoginDto
  → RateLimiter 检查（per-IP + per-email）
  → LoginUseCase
      → UsersFacade.findByNormalizedEmail(email)
           └ 不存在 → InvalidCredentialsError（精确错误策略，见 §11.4）
      → PasswordHasher.verify(hash, password)
           └ 不匹配 → InvalidCredentialsError
      → User.status == DISABLED → UserDisabledError
      → 生成 access JWT（claims 见 §6.4）
      → 生成 opaque refresh 串：随机 256bit → base64url；SHA-256 哈希
           familyId = 新 UUIDv7（每次登录新家族）
      → 事务：insert refresh_tokens(familyId, tokenHash, expiresAt=+7d)
  → 响应 { accessToken, user } + Set-Cookie: refresh（§6.5）
```

> 密码哈希校验（CPU）在事务外完成（基线文档 §7.2/§11.1）。

### 6.2 刷新 `POST /v1/auth/refresh`（读 Cookie）

```text
请求（Cookie: refresh=xxx）
  → RefreshTokenUseCase
      → tokenHash = sha256(cookie 值)
      → 查 refresh_tokens by tokenHash（事务内，SELECT ... FOR UPDATE）
        ┌ 未找到 / revokedAt 非空且 replacedById 为空（登出/已注销）
        │     → RefreshTokenInvalidError（401，仅失效当前）
        ├ revokedAt 非空且 replacedById 非空（已轮换 token 被重用）
        │     → 重放！→ UPDATE 同 familyId 且 revokedAt IS NULL 的全部行置 revokedAt=now
        │     → RefreshTokenReplayError（401，吊销整个家族）
        ├ expiresAt < now → RefreshTokenInvalidError
        └ 有效：
              → 生成新 opaque token（同 familyId）
              → 事务：旧行 revokedAt=now, replacedById=新id；insert 新行
  → 响应 { accessToken } + Set-Cookie: 新 refresh（轮换）
```

**重放检测要点**：一个已被轮换的 token 再次出现 = 令牌可能被盗（合法客户端轮换后只会持有新 token）。立即吊销该 `familyId` 下所有未失效 token，迫使该登录链上所有设备重新登录。

### 6.3 登出 `POST /v1/auth/logout`

```text
请求（Cookie: refresh=xxx）
  → LogoutUseCase
      → tokenHash = sha256(cookie 值)
      → 事务：UPDATE by tokenHash SET revokedAt=now（仅注销当前设备）
  → 响应 204 + Set-Cookie: refresh=; Max-Age=0（清 Cookie）
```

> 「全部登出」端点（按 userId 吊销所有 family）= 后续迭代。

### 6.4 access JWT claims

```jsonc
{
  "sub": "<user uuid>",
  "role": "ADMIN",
  "perms": ["user:read", "user:create", "user:update", "user:disable", "user:role:assign"],
  "type": "access",
  "iss": "<JWT_ISSUER>",
  "aud": "<JWT_ACCESS_AUDIENCE>",
  "iat": 178...,
  "exp": 178...   // iat + 900s（15min）
}
```

- `SUPER_ADMIN`：`role:"SUPER_ADMIN"`，Guard 直接 bypass（不依赖 `perms`）。
- 严格校验：`iss` / `aud` / `exp` / `alg`（固定 HS256 或 RS256，见 §10.2）；拒收无 `kid` 之外的算法变体。

### 6.5 refresh Cookie 配置

| 属性 | 值 | 说明 |
| --- | --- | --- |
| `HttpOnly` | `true` | JS 不可读，防 XSS 窃取 |
| `Secure` | `true`（生产） | 仅 HTTPS 传输；本地开发可关 |
| `SameSite` | `Lax` | 阻断跨站 POST（refresh/logout 受保护） |
| `Path` | `/auth` | 仅认证端点携带，缩小暴露面 |
| `Max-Age` | `604800`（7d） | 与 `expiresAt` 一致 |
| 名称 | `refresh_token` | 固定 |

> CSRF：access 走 `Authorization: Bearer`，不受 CSRF 影响；refresh/logout 走 Cookie，由 `SameSite=Lax` + 仅 `POST` 方法 + 仅 `/auth` 路径共同收窄。若未来放宽 SameSite，需补 double-submit token。

---

## 7. RBAC 与授权

### 7.1 角色与权限码

```ts
export const PermissionCode = {
  USER_READ: 'user:read',
  USER_CREATE: 'user:create',
  USER_UPDATE: 'user:update',
  USER_DISABLE: 'user:disable',     // 启用/禁用
  USER_ROLE_ASSIGN: 'user:role:assign',
} as const;
```

### 7.2 `ROLE_PERMISSIONS` 硬编码映射

```ts
export const ROLE_PERMISSIONS: Record<UserRole, readonly PermissionCode[] | '*'> = {
  SUPER_ADMIN: '*',   // Guard 直接 bypass（见 §7.3）
  ADMIN:     ['user:read','user:create','user:update','user:disable','user:role:assign'],
  OPERATOR:  ['user:read','user:create','user:update'],
  VIEWER:    ['user:read'],
};
```

> 权限码与映射是**代码单一事实来源**（扁平角色决策的自然结果）。调整权限 = 改代码 + 重新部署（已接受）。

### 7.3 SUPER_ADMIN 保护（扁平 enum 下的领域不变量）

> 协调说明：访谈中「超管保护」预览出现 `isSystem` 角色列，但 RBAC 选了扁平 enum（无 Role 表）。**协调方案**：以领域规则 + 应用层校验替代 DB 列。

- **bypass**：`SUPER_ADMIN` 在 `JwtAuthGuard` 与 `UserPolicy` 中直接放行，不查 `perms`。
- **最后一个超管不可移除**：
  - 禁用、删除、改角色（改离 `SUPER_ADMIN`）前，在**可序列化事务**内 `COUNT(*) WHERE role='SUPER_ADMIN' AND status='ACTIVE'`，若结果 ≤1 且本次会使其减少 → `LastSuperAdminError`。
  - 防自锁（仅管理员创建账号、无自助恢复路径，此为最后防线）。
- **不可降级受保护目标**：非 `SUPER_ADMIN` 不可对 `SUPER_ADMIN` 目标执行 disable/改角色（对象级 Policy，§7.5）。

### 7.4 路由级守卫（默认拒绝）

- `JwtAuthGuard` **全局注册**，所有路由默认受保护；公开端点用 `@Public()` 显式标记（基线文档 §13.1）。
- `@RequirePermissions('user:create')` 声明所需权限；Guard 流程：
  1. 取 access token → 验签 + 校验 `iss/aud/exp/alg` → 失败 401。
  2. `role === SUPER_ADMIN` → 放行。
  3. 否则检查 `perms ⊇ required` → 不满足 403。
- `@CurrentUser()` 装饰器从已验证 token 注入 `AuthUser { id, role, perms }`。

### 7.5 对象级 `UserPolicy`

```ts
// 概念示意
class UserPolicy {
  can(actor: AuthUser, action: 'update'|'disable'|'assignRole', target: User): boolean {
    if (actor.role === 'SUPER_ADMIN') return true;
    // 非超管不可操作超管目标
    if (target.role === 'SUPER_ADMIN') return false;
    // 不可操作与自己同级或更高权限的目标（按角色等级排序）
    if (rank(target.role) >= rank(actor.role)) return false;
    return true;
  }
}
```

在用例内调用：`this.userPolicy.can(actor, 'disable', target)`；不满足抛 `InsufficientPrivilegeError`（403）。「最后一个超管」不变量在用例事务内单独校验。

---

## 8. Prisma 基础设施（前置改造）

### 8.1 `DatabaseModule`（替代当前 `@Global() PrismaModule`）

- 收敛当前散落的 `PrismaService`（直读 `process.env.DATABASE_URL`）为显式 `DatabaseModule`。
- `DatabaseClient extends PrismaClient`，构造注入 `PrismaPg` 适配器，连接串/池参数来自配置层（基线文档 §8.2）。
- **不**设 `@Global()`；业务模块显式 `imports: [DatabaseModule]`。
- 仅 Infrastructure 持久化适配器注入 `DatabaseClient`。
- `onModuleInit` `$connect`；`onApplicationShutdown` `$disconnect`。

### 8.2 Unit of Work（事务边界在用例）

- `PrismaUsersUnitOfWork`/`PrismaIdentityUnitOfWork` 实现 `run` 与 `runSerializable`（基线文档 §8.4/§11.3）。
- 「读后写」（创建用户查重、刷新轮换、最后超管计数）用 `runSerializable` + 有界重试（`P2034`/`40001`/`40P01`）。
- `TransactionClient` 不越过基础设施边界。

### 8.3 Repository + Mapper

- `PrismaUserRepository` / `PrismaRefreshTokenRepository` 仅声明所用 delegate。
- `PrismaUserMapper` / `PrismaRefreshTokenMapper` 集中 record↔domain 转换，Prisma 类型不外泄。
- 持久化错误归一：`mapPrismaPersistenceError()` 把 `P2002` → `UserEmailAlreadyUsedError`，其余未知错误记一次完整日志后抛通用 `500`（基线文档 §6.4）。

---

## 9. API 契约

### 9.1 端点清单（MVP）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/v1/auth/login` | `@Public()` | 登录；返回 access + set refresh cookie |
| POST | `/v1/auth/refresh` | `@Public()`（凭 refresh cookie） | 轮换 refresh、返回新 access |
| POST | `/v1/auth/logout` | 已认证 | 注销当前设备 refresh |
| GET | `/v1/auth/me` | 已认证 | 返回当前用户 + `perms[]` |
| POST | `/v1/users` | `user:create` | 管理员创建用户 |
| GET | `/v1/users` | `user:read` | 列表（cursor 分页） |
| GET | `/v1/users/:id` | `user:read` | 详情 |
| PATCH | `/v1/users/:id/role` | `user:role:assign` | 改角色（对象级 Policy + 最后超管校验） |
| POST | `/v1/users/:id/disable` | `user:disable` | 禁用（对象级 Policy + 最后超管校验） |
| POST | `/v1/users/:id/enable` | `user:disable` | 启用 |

> 「PATCH 通用更新」按基线文档 §6.2 拆为具体动作端点；邮箱/密码修改等留后续迭代。

### 9.2 请求/响应样例

**登录**
```http
POST /v1/auth/login
Content-Type: application/json

{ "email": "alice@apex.local", "password": "correct-horse-battery-9" }
```
```http
HTTP/1.1 200 OK
Content-Type: application/json
Set-Cookie: refresh_token=<opaque>; HttpOnly; Secure; SameSite=Lax; Path=/auth; Max-Age=604800

{
  "data": {
    "accessToken": "eyJhbGci...",
    "tokenType": "Bearer",
    "expiresIn": 900,
    "user": { "id": "...", "email": "alice@apex.local", "role": "ADMIN", "status": "ACTIVE" }
  }
}
```

**`/auth/me`**
```json
{ "data": { "user": { /* … */ }, "permissions": ["user:read","user:create","user:update","user:disable","user:role:assign"] } }
```

> 前端可从 access JWT 解码 `perms` 渲染按钮，也可调 `/auth/me` 取权威集合（二者一致，`/me` 便于初始化）。

**创建用户（管理员）**
```http
POST /v1/users
Authorization: Bearer <access>
Content-Type: application/json

{ "email": "bob@apex.local", "password": "very-strong-pw-123", "role": "OPERATOR" }
```
```http
HTTP/1.1 201 Created
Location: /v1/users/<id>
{ "data": { "id": "...", "email": "bob@apex.local", "role": "OPERATOR", "status": "ACTIVE" } }
```

### 9.3 列表分页

- Keyset/Cursor 分页（基线文档 §10.2）：`orderBy: [{createdAt:'desc'},{id:'desc'}]`，`take: pageSize+1`。
- 入参 `pageSize`（上限 100）、`cursor`（base64 编码 `{createdAt,id}`）。
- 响应 `{ data: [...], meta: { nextCursor, hasMore } }`。

### 9.4 错误模型（Problem Details，RFC 9457）

`Content-Type: application/problem+json`
```json
{
  "type": "https://apex.example.com/problems/invalid-credentials",
  "title": "邮箱或密码错误",
  "status": 401,
  "code": "INVALID_CREDENTIALS",
  "traceId": "01JZZZZZZZZZZZZZZZZZZZZZZZ",
  "errors": []
}
```
- 字段级校验失败时 `errors[]` 填字段路径 + 错误码。
- 全局异常过滤器统一映射：领域错误→对应 HTTP；未知错误→500 + 完整堆栈仅记一次日志，不外泄（基线文档 §6.4）。
- HTTP 语义遵循基线文档 §6.3（201/200/204/400/401/403/404/409/422/429/500）。

---

## 10. 配置（启动校验）

### 10.1 zod schema（`platform/config/schema.ts`）

启动时 `safeParse(process.env)`，失败即拒绝启动（基线文档 §5.1）。关键变量：

| 变量 | 说明 | 示例/默认 |
| --- | --- | --- |
| `NODE_ENV` | 环境 | `production` |
| `HTTP_HOST` / `PORT` | 监听 | `0.0.0.0` / `3000` |
| `DATABASE_URL` | 运行时连接串（NestJS 配置层管理） | `postgresql://...` |
| `MIGRATION_DATABASE_URL` | Prisma CLI 直连（`prisma.config.ts`） | `postgresql://...` |
| `JWT_ACCESS_SECRET` | access 签名密钥（≥32 bytes） | Secret Manager |
| `JWT_REFRESH_SECRET` | （保留，opaque refresh 当前不用其签名；预留） | Secret Manager |
| `JWT_ISSUER` / `JWT_ACCESS_AUDIENCE` | 签发者/受众 | `apex-admin` / `apex-admin-web` |
| `JWT_ACCESS_TTL_SECONDS` | access 有效期 | `900` |
| `JWT_REFRESH_TTL_SECONDS` | refresh 有效期 | `604800` |
| `ARGON2_MEMORY_KIB` / `ARGON2_TIME_COST` / `ARGON2_PARALLELISM` | Argon2id 参数 | `65536` / `3` / `1` |
| `RATE_LIMIT_LOGIN_PER_IP` / `RATE_LIMIT_LOGIN_PER_EMAIL` / `RATE_LIMIT_WINDOW_SECONDS` | 登录限流 | `10` / `5` / `60` |
| `CORS_ORIGINS` | 允许来源（逗号分隔白名单） | `https://admin.apex.local` |
| `COOKIE_SECURE` | Cookie Secure 标志 | `true`（生产） |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` | **仅 bootstrap Job**（§13） | Secret Manager |

### 10.2 密钥与算法

- 单静态对称密钥（MVP 不启用 kid/JWKS）。
- 算法固定（建议 `HS256`；若团队偏好非对称则 `RS256`，密钥同样单静态）。
- 密钥长度校验在 zod schema（≥32 bytes）。
- **轮换策略**：更换 `JWT_ACCESS_SECRET` 需重启，所有 access 在 ≤15min 内自然失效；refresh 为不透明串、入库哈希，与 JWT 密钥解耦，不受其轮换影响。

---

## 11. 安全设计

### 11.1 密码哈希

- Argon2id，参数集中配置（`m=64MiB, t=3, p=1`，OWASP 基线），可升级。
- 哈希在事务**外**完成（基线文档 §7.2）。
- `verify` 使用常量时间比较。

### 11.2 密码策略

- 长度 12–128；不强制大小写/数字/符号（遵循 NIST 800-63B「长度优先」）。
- 不接 HIBP（已接受，后续可加）。

### 11.3 登录限流（`/v1/auth/login`）

- 双维度滑动窗口（60s）：per-IP ≤10、per-email ≤5。
- 命中即 429 + `Retry-After`。
- 实现：`RateLimiter` Port，MVP 内存实现（单副本）；多副本时换 Redis 实现，不改调用方。
- 限流计数不依赖目标账号是否存在（避免被当作枚举探测）。

### 11.4 用户枚举（已接受风险）

- **决策**：实用优先，精确错误。
  - 登录：`USER_NOT_FOUND` 与 `INVALID_CREDENTIALS` 分开返回（便于管理员排查）。
  - 创建用户：邮箱已占用 → 明确 `409 USER_EMAIL_ALREADY_USED`。
- **风险记录**：内部后台、账号仅管理员创建、登录限流兜底，枚举面可接受。
- ** revisit 条件**：若任何端点开放公网（自助注册/找回），必须改为统一模糊错误。

### 11.5 凭证与 Cookie

- access 走 `Authorization: Bearer`，不进 Cookie → 无 CSRF 面。
- refresh 进 httpOnly + SameSite=Lax + Secure + Path=/auth Cookie（§6.5）。
- CORS：显式来源白名单（`CORS_ORIGINS`），`credentials: true`。
- 全站 HTTPS（生产），启用 HSTS、helmet。

### 11.6 其他

- `ValidationPipe`：`whitelist + forbidNonWhitelisted + transform`，禁隐式转换。
- 请求体/分页/字段长度上限。
- 不记录密码、token、Authorization 头、Cookie（基线文档 §17.1）。
- `SUPER_ADMIN` 保护见 §7.3。

---

## 12. 事务与并发

| 用例 | 隔离 | 要点 |
| --- | --- | --- |
| 创建用户 | `runSerializable` | 事务内查 `emailNormalized` 重复 → `add`；DB 唯一约束兜底 |
| 登录 | 短事务 | 仅 insert refresh_tokens；凭证校验在事务外 |
| 刷新轮换 | `runSerializable` + `FOR UPDATE` | 旧行 `revokedAt`/`replacedById`、新行插入原子完成；重放 → 吊销 family |
| 登出 | 短事务 | `UPDATE revokedAt` |
| 改角色 / 启停 | `runSerializable` | 事务内 `COUNT` 活跃超管 + `UserPolicy` 对象级判定 + 乐观锁 `version` |

- 事务内禁止外部调用、密码哈希、长计算（基线文档 §11.1）。
- Serializable 冲突（`P2034`/`40001`/`40P01`）有界重试 + 抖动；`P2002` 不重试（业务唯一冲突）。

---

## 13. Bootstrap：首个 SUPER_ADMIN

- **方式**：env 驱动 seed。
- **执行者**：独立部署 Job（**非**每次应用启动自动跑；与 Migration Job 分离，基线文档 §12.5）。
- **幂等**：`SUPER_ADMIN_EMAIL` 不存在则创建，存在则跳过；不覆盖已有账号。
- **流程**：
  1. 读取 `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD`（Secret Manager 注入，不入镜像/仓库/日志）。
  2. Argon2id 哈希密码（事务外）。
  3. `upsert`（`where: {emailNormalized}`，`create` only；已存在则不动）。
- **脚本**：`pnpm seed:super-admin`（对应 `scripts/seed-super-admin.ts`），生产由部署流水线触发。
- 安全：secret 仅在运行环境；失败不回显密码。

---

## 14. 错误码目录

见 §5.3 表格。所有码稳定、大写下划线、可被前端依赖。CI 应校验错误码目录与代码一致（后续）。

---

## 15. 测试策略（集成 + E2E 为主）

遵循基线文档 §15（真实 PostgreSQL，禁 SQLite）。扁平 enum 领域逻辑少，**省略纯 domain 单测**，重点投入集成与 E2E。

### 15.1 集成测试（Testcontainers + 真实 PG）

- 从空库执行完整 `prisma migrate deploy`。
- `users.email_normalized` 唯一约束：并发创建同邮箱 → 恰好一个 409。
- 创建用户 Mapper 往返。
- **刷新轮换**：登录 → refresh → 旧 token 仍可识别为「已轮换」；用旧 token 再次 refresh → 触发 `REFRESH_TOKEN_REPLAY` 且整个 family 被吊销（新 token 也失效）。
- **登出注销** vs **轮换**：登出的 token 重用 → 仅 401，不吊销 family。
- **最后一个超管**：并发/串行禁用、改角色、（未来）删除最后一个 `SUPER_ADMIN` → `LAST_SUPER_ADMIN`。
- 乐观锁 `version` 冲突 → 409。
- 迁移产生的 CHECK 约束生效。

### 15.2 E2E（HTTP 边界）

- 登录成功 → 200 + access + Set-Cookie（含正确属性）。
- 登录失败：密码错 → `INVALID_CREDENTIALS`；账号不存在 → `USER_NOT_FOUND`；禁用 → `USER_DISABLED`。
- access 过期 → `/auth/refresh` → 新 access。
- `@Public()` 端点无需 token；受保护端点无 token → 401。
- `OPERATOR` 调 `PATCH /users/:id/role` → 403（路由级）。
- `OPERATOR` 对 `ADMIN` 目标调 disable → 403（对象级 Policy）。
- 非 `SUPER_ADMIN` 对 `SUPER_ADMIN` 目标 → 403。
- 创建用户邮箱重复 → 409。
- 登录限流触发 → 429 + `Retry-After`。
- Problem Details 结构、`traceId`、未知字段被拒。
- 全局 Guard/Pipe/Filter/信封行为。

### 15.3 测试数据

- Builder/Factory 创建最小场景；不从生产复制个人数据。
- 独立数据库/Worker，不共享状态。
- Argon2id 在测试中可用低参数加速（仅在测试环境，配置覆盖），但映射与校验逻辑不变。

---

## 16. 现状与前置改造（P0）

当前仓库为脚手架状态，需先建立正确边界（基线文档 §22 P0）：

1. **tsconfig 严格模式**：开启 `strict`、`noImplicitAny:true`、`strictBindCallApply`、`noFallthroughCasesInSwitch`、`noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`（当前多处关闭）。
2. **`.env` 安全**：当前 `.env` 含**真实数据库地址与明文密码**（`postgresql://postgres:...@47.x.x.x/apex_admin`）——**必须移出版本控制**，改提交 `.env.example`（无真实值），真实凭证走 Secret Manager。建议立即 `git rm --cached .env` 并确认 `.gitignore`。
3. **`prisma.config.ts`**：改读 `MIGRATION_DATABASE_URL`（当前读 `DATABASE_URL`），与运行时配置层职责分离（基线文档 §5.1/§8.1）。
4. **`DatabaseModule`**：移除 `@Global()` PrismaModule，改造为显式 `DatabaseModule` + `DatabaseClient`（配置驱动）。
5. **配置层**：引入 `@nestjs/config` + zod（§10）。
6. **`main.ts` → `bootstrap/`**：`helmet`、CORS（白名单）、全局 `ValidationPipe`、URI 版本化、`enableShutdownHooks`、结构化 Logger。
7. **全局基础设施**：请求 ID 中间件、Problem Details 异常过滤器、`{data}` 信封拦截器、健康检查（live/ready）。
8. **lint**：将 `lint`（带 `--fix`）拆为只读 `lint:check` + 显式 `lint:fix`；关闭/收紧 `no-explicit-any`。
9. **脚本**：补 `prisma:generate` / `prisma:migrate:deploy` / `seed:super-admin`。

---

## 17. 未引入与已接受取舍

- **Transactional Outbox**：MVP 无异步副作用（不发邮件、无消息），**不引入** Outbox。未来加入邮件通知（如登录提醒）时再按基线文档 §11.7 启用。
- **审计日志**：MVP 仅运行日志，**不建**独立审计表/链。后续按基线文档 §13.6 补充。
- **强制首登改密**：管理员创建账号设定初始密码，需带外告知用户；无强制改密机制——**已接受风险**，记入遗留项。
- **多副本限流**：MVP 单副本内存计数；`RateLimiter` Port 化，多副本时换 Redis 实现。
- **JWT kid 轮换**：单静态密钥；轮换需重启。

---

## 18. 交付里程碑

1. **P0 前置改造**（§16）：严格 tsconfig、`.env` 脱敏、DatabaseModule、配置层、bootstrap、全局过滤器/信封、lint 拆分。
2. **数据层**：Prisma schema（User/RefreshToken/枚举/约束/索引）+ 首个迁移 + CHECK 迁移。
3. **users 模块**：值对象、聚合、Ports、Prisma 适配器+Mapper、UnitOfWork、用例（创建/列表/详情/改角色/启停）、`UserPolicy`、Controller+DTO。
4. **identity 模块**：JWT 服务、opaque refresh 生成+哈希、限流、登录/刷新/登出/me 用例、全局 Guard + 装饰器。
5. **bootstrap**：`seed:super-admin` 脚本。
6. **测试**：集成（Testcontainers）+ E2E（§15）。
7. **文档**：模块 README、错误码目录、OpenAPI。

---

## 19. 未决事项（需后续确认）

- JWT 签名算法：`HS256`（对称，简单）vs `RS256`（非对称，适合多验证方）——MVP 默认 `HS256`，待团队确认。
- `UserRole` 各角色具体权限映射（§7.2）为建议基线，需产品确认是否增减权限码。
- 速率限制具体阈值（per-IP 10、per-email 5）需结合压测调优。
- 生产 Cookie 域名（`COOKIE_DOMAIN`）由部署域名决定。
