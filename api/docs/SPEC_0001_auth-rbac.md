# SPEC-0001：账号开通、登录、会话与 RBAC 权限控制

> 状态：已定稿（2026-07-14 架构与安全评审修订）  
> 日期：2026-07-14  
> 适用工程：`api`（NestJS 11 + Prisma 7.8 + PostgreSQL）  
> 架构基线：遵循 [`NestJS-Prisma-PostgreSQL-最佳实践.md`](./NestJS-Prisma-PostgreSQL-最佳实践.md)（下文简称「基线文档」）  
> 范围：单组织内部后台的 MVP 核心身份、会话与授权主链路

---

## 0. 决策摘要

| 维度 | 决策 | 关键取舍 |
| --- | --- | --- |
| 系统拓扑 | **单组织内部后台** | 无 `tenant_id`；邮箱为小写规范值并全局唯一 |
| 账号开通 | **仅已认证且有权的后台用户创建** | 无公开注册端点；独立 bootstrap Job 创建首个超管 |
| 一致性边界 | **单一 `iam` 模块** | 账户、凭证、会话、授权属于同一业务边界；模块内部按子域分包 |
| 交付范围 | **MVP 核心** | 账号开通、登录、刷新、登出、`/me`、RBAC、最小安全审计 |
| 令牌传输 | **双 Token** | access JWT（Bearer）+ opaque refresh Cookie |
| Session 模型 | **显式 `AuthSession` + RefreshToken 状态机** | 每次登录创建一个设备会话；绝对 7 天过期；轮换、撤销、重放检测 |
| Refresh 并发 | **5 秒并发宽限 + 超时重放吊销** | 宽限期内旧 token 返回 409 且不吊销；宽限期外重用吊销整个 Session |
| RBAC 模型 | **扁平固定角色** | `SUPER_ADMIN/ADMIN/OPERATOR/VIEWER`；权限码与角色等级均为代码事实来源 |
| 权限解析 | **access token 权限快照** | 服务端授权 0 次查库；收权与角色变化最迟在 access TTL（24h）后生效 |
| 授权层级 | **认证、路由授权、对象授权分离** | `AccessTokenGuard` + `PermissionsGuard` + `UserPolicy` |
| 超管保护 | **seed + 领域/应用不变量** | 禁止自改角色/状态；最后一个活跃超管不可移除 |
| 密码策略 | **Argon2id + 长度 + blocklist** | 单因素密码长度 15–128；不设复杂度规则；支持空格与 Unicode |
| 登录限流 | **IP + 账号 + 哈希并发上限** | 60s 窗口；per-IP 10、per-email 5；Argon2 并发默认 4 |
| Cookie/CSRF | **Host-only Cookie + 可信 Origin 校验** | `Path=/v1/auth`；不设置 Domain；CORS 不替代 CSRF 防护 |
| JWT | **HS256 单静态密钥** | 固定算法、无 `kid`；轮换时协调重启，旧 access 立即失效 |
| 配置 | **按进程入口独立 zod schema** | Runtime、Migration、Seed 不共享无关必填变量 |
| 测试 | **单元 + 真实 PG 集成 + E2E + 架构测试** | 权限矩阵和状态机单测；并发与事务行为用真实 PostgreSQL |

---

## 1. 范围

### 1.1 本期交付（In Scope）

- 有权限的后台用户创建账号。
- 登录、刷新、登出、获取当前用户（`/v1/auth/me`）。
- access/refresh 双令牌签发、轮换、撤销、并发宽限与重放检测。
- 显式设备会话 `AuthSession`；禁用用户时原子吊销其全部活跃 Session。
- 扁平角色 RBAC：路由权限 Guard + 对象级 Policy。
- 默认拒绝的全局认证与授权 Guard。
- 首个 `SUPER_ADMIN` 的幂等 bootstrap。
- Argon2id 密码哈希、弱密码 blocklist、登录限流、哈希并发保护。
- Cookie 可信 Origin 校验与明确的 CSRF 边界。
- 最小持久化安全审计：账号创建、角色/状态变化、Session 创建/撤销、refresh 重放。
- 真实 PostgreSQL 集成测试、关键 HTTP E2E、领域/应用单元测试和架构测试。

### 1.2 暂不交付（Out of Scope）

- 密码找回/重置。
- 邮箱验证、邀请链接激活。
- 连续失败次数锁定与账号恢复流程。
- 强制首登改密。
- MFA、双因素与 step-up 认证。
- 完整合规审计平台、WORM 存储和审计导出。
- Transactional Outbox；本期没有邮件或消息副作用。
- JWT `kid` 轮换、JWKS。
- 多副本分布式限流。
- 多租户、RLS。
- 用户删除；本期只有启用/禁用。

### 1.3 明确接受的风险与部署约束

- 登录返回精确错误，存在用户枚举面；系统必须保持内部可控网络暴露并启用限流。
- 管理员设置初始密码且没有强制首登改密；必须通过安全带外渠道交付。
- 已签发 access token 在用户禁用、角色降级或权限映射收紧后，最多继续有效 24 小时。
- access token 不随 logout 立即失效；logout 立即吊销 refresh Session，access 自然过期。
- 单静态 JWT 密钥轮换会让旧 access 立即失效；部署必须协调重启，不能滚动混用两个无 `kid` 的密钥。
- MVP 内存限流只允许单应用副本；扩容前必须先完成 Redis 限流规格与实现，不提供静默 fallback。
- 特权账号尚未启用 MFA；若系统开放公网或进入高敏生产环境，MFA 是发布前置条件。

---

## 2. 系统拓扑与安全边界

- 单组织内部后台，无租户概念。
- 所有用户共享全局账号空间；`users.email` 存储小写、trim 后的规范值并全局唯一。
- 客户端提交的角色、权限和用户状态均不可信。
- access JWT 是短期授权快照；PostgreSQL 是账户和 Session 状态的唯一事实来源。
- 每次登录创建独立 Session；多个设备互不共享 Session。
- 若未来演进为多租户 SaaS，必须重新设计唯一约束、授权上下文、缓存 key、审计和 RLS；本期不承诺平滑迁移。

---

## 3. 架构与模块边界

账户、密码凭证、Refresh Session 与 RBAC 频繁参与同一用例和事务，属于同一个一致性边界。禁止为了展示模块模式而拆成相互调用的 `identity` 与 `users` 模块。

`iam` 是业务模块；账户、会话、授权是模块内部的高内聚子域，不是可绕过公开 API 的独立模块。

```text
src/
├─ main.ts                         # 稳定进程入口，仅调用 bootstrap()
├─ bootstrap/
│  ├─ bootstrap.ts                # Nest 应用创建与生命周期
│  └─ setup-application.ts        # helmet/cors/pipe/versioning/guard/filter
├─ platform/
│  ├─ config/                     # Runtime 强类型配置
│  ├─ database/                   # DatabaseModule + DatabaseClient
│  ├─ logging/                    # 结构化日志、请求 ID、集中脱敏
│  ├─ observability/              # live/ready、指标
│  └─ http/                       # Pipe、Problem Details、响应信封
├─ modules/
│  └─ iam/
│     ├─ iam.module.ts
│     ├─ public-api.ts            # 只导出稳定跨模块契约
│     ├─ presentation/http/
│     │  ├─ auth.controller.ts
│     │  ├─ users.controller.ts
│     │  ├─ guards/
│     │  │  ├─ access-token.guard.ts
│     │  │  ├─ permissions.guard.ts
│     │  ├─ decorators/
│     │  └─ dto/
│     ├─ application/
│     │  ├─ contracts/            # AuthenticatedActor、Command、Result
│     │  ├─ use-cases/
│     │  │  ├─ accounts/
│     │  │  └─ sessions/
│     │  ├─ policies/
│     │  │  └─ user.policy.ts
│     │  └─ ports/                # Repository、UoW、Hasher、Clock、Audit
│     ├─ domain/
│     │  ├─ account/
│     │  ├─ session/
│     │  ├─ authorization/
│     │  └─ errors/
│     └─ infrastructure/
│        ├─ persistence/prisma/
│        ├─ jwt/
│        ├─ crypto/
│        └─ ratelimit/
└─ shared/kernel/                 # Clock、ID、分页等真正稳定抽象
```

### 3.1 所有权

| 模块 | 拥有的数据 | 对外公开 |
| --- | --- | --- |
| `iam` | `users`、`auth_sessions`、`refresh_tokens`、`security_audit_events` | Guard、装饰器、`AuthenticatedActor` 只读契约、必要 Facade |

### 3.2 分层依赖

- Domain 为纯 TypeScript，不导入 NestJS、Prisma、HTTP、日志或环境变量。
- Application 依赖 Domain 和抽象 Port，不导入 Prisma 类型或 Presentation DTO。
- Presentation 只做协议转换、认证上下文提取和用例调用。
- Infrastructure 实现 Port，并集中完成 Prisma/领域映射。
- `AuthenticatedActor` 定义在 Application contract，不定义在 Guard 或 Decorator 文件中。
- `iam.module.ts` 只负责依赖组装，不包含业务规则。
- 事务上下文通过 UoW 回调显式传递，禁止 AsyncLocalStorage 或单例成员保存当前事务。

### 3.3 Guard 与 Policy 职责

- `AccessTokenGuard`：只回答“是谁”，负责 JWT 提取、验证和注入 actor。
- `PermissionsGuard`：只回答“是否具备路由能力”，验证 `@RequirePermissions`。
- `UserPolicy`：回答“能否对这个具体用户执行动作”，在 Use Case 内调用。
- `SUPER_ADMIN` 的权限 bypass 只存在于授权层，绝不绕过 JWT 验签或账号输入校验。

---

## 4. 数据模型（Prisma Schema）

沿用 [ADR-0001](./adr/0001-prisma-generator-choice.md) 确定的 `prisma-client-js` + `@prisma/adapter-pg` 方案；Prisma 类型只存在于 Infrastructure。

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
}

/// 扁平固定角色。
///
/// 角色等级与权限映射由授权领域代码显式定义，
/// 禁止依赖数据库枚举顺序进行大小比较。
enum UserRole {
  SUPER_ADMIN
  ADMIN
  OPERATOR
  VIEWER

  @@map("user_role")
}

/// 用户生命周期状态。
///
/// DISABLED 用户不能登录或刷新；禁用动作会在同一事务内
/// 吊销该用户的全部活跃 Session。
enum UserStatus {
  ACTIVE
  DISABLED

  @@map("user_status")
}

/// Session 的显式业务状态。
///
/// 过期仍由 expiresAt 判定；主动失效统一进入 REVOKED，
/// 并必须同时记录撤销时间与稳定原因码。
enum AuthSessionStatus {
  ACTIVE
  REVOKED

  @@map("auth_session_status")
}

/// Refresh Token 的显式状态机。
///
/// ACTIVE 可以轮换或撤销；ROTATED 再次出现时按时间窗口判断
/// 并发陈旧请求或重放；REVOKED 只表示显式注销的当前 token。
enum RefreshTokenStatus {
  ACTIVE
  ROTATED
  REVOKED

  @@map("refresh_token_status")
}

/// Session 的稳定撤销原因。
///
/// 原因码参与审计与问题定位，不能用自由文本替代。
enum SessionRevocationReason {
  LOGOUT
  REFRESH_TOKEN_REPLAY
  USER_DISABLED

  @@map("session_revocation_reason")
}

/// 最小持久化安全审计动作。
///
/// 只列出本期真实产生的安全状态变化，不为未来功能预留枚举。
enum SecurityAuditAction {
  USER_CREATED
  USER_ROLE_CHANGED
  USER_STATUS_CHANGED
  SESSION_CREATED
  SESSION_REVOKED
  REFRESH_REPLAY_DETECTED

  @@map("security_audit_action")
}

/// 用户聚合根。
///
/// email 是唯一持久化邮箱值：trim 后转小写。
/// 不同时存储 emailNormalized，避免两个字段产生不一致状态。
model User {
  id           String     @id @db.Uuid
  email        String     @unique(map: "uq_users_email") @db.VarChar(320)
  passwordHash String     @map("password_hash")
  role         UserRole
  status       UserStatus @default(ACTIVE)
  createdAt    DateTime   @default(now()) @map("created_at") @db.Timestamptz(3)
  updatedAt    DateTime   @updatedAt @map("updated_at") @db.Timestamptz(3)

  sessions     AuthSession[]

  @@index([role, status], map: "idx_users_role_status")
  @@map("users")
}

/// 一次登录对应一个设备 Session。
///
/// expiresAt 是绝对过期时间，Refresh 轮换不会延长它。
/// status 与 revokedAt/revocationReason 由数据库 CHECK 共同约束。
model AuthSession {
  id               String                   @id @db.Uuid
  userId           String                   @map("user_id") @db.Uuid
  status           AuthSessionStatus        @default(ACTIVE)
  expiresAt        DateTime                 @map("expires_at") @db.Timestamptz(3)
  revokedAt        DateTime?                @map("revoked_at") @db.Timestamptz(3)
  revocationReason SessionRevocationReason? @map("revocation_reason")
  createdAt        DateTime                 @default(now()) @map("created_at") @db.Timestamptz(3)

  user             User                     @relation(fields: [userId], references: [id], onDelete: Restrict)
  refreshTokens    RefreshToken[]

  @@index([userId, status], map: "idx_auth_sessions_user_status")
  @@index([expiresAt], map: "idx_auth_sessions_expires")
  @@map("auth_sessions")
}

/// 不透明 Refresh Token 的持久化记录。
///
/// 只保存 SHA-256 哈希，不保存原始 token。
/// 每个 Session 在任意时刻最多存在一个 ACTIVE token。
model RefreshToken {
  id         String             @id @db.Uuid
  sessionId  String             @map("session_id") @db.Uuid
  tokenHash  String             @unique(map: "uq_refresh_tokens_hash") @map("token_hash") @db.Char(64)
  status     RefreshTokenStatus @default(ACTIVE)
  rotatedAt  DateTime?          @map("rotated_at") @db.Timestamptz(3)
  revokedAt  DateTime?          @map("revoked_at") @db.Timestamptz(3)
  createdAt  DateTime           @default(now()) @map("created_at") @db.Timestamptz(3)

  session    AuthSession        @relation(fields: [sessionId], references: [id], onDelete: Cascade)

  @@index([sessionId, status], map: "idx_refresh_tokens_session_status")
  @@map("refresh_tokens")
}

/// 追加写的最小安全审计记录。
///
/// 历史 actor/target/session ID 故意不建外键，
/// 避免未来数据保留策略删除业务记录时破坏审计历史。
model SecurityAuditEvent {
  id           String              @id @db.Uuid
  action       SecurityAuditAction
  actorUserId  String?             @map("actor_user_id") @db.Uuid
  targetUserId String?             @map("target_user_id") @db.Uuid
  sessionId    String?             @map("session_id") @db.Uuid
  previousRole UserRole?           @map("previous_role")
  nextRole     UserRole?           @map("next_role")
  previousStatus UserStatus?       @map("previous_status")
  nextStatus     UserStatus?       @map("next_status")
  revocationReason SessionRevocationReason? @map("revocation_reason")
  correlationId String             @map("correlation_id") @db.VarChar(64)
  createdAt    DateTime            @default(now()) @map("created_at") @db.Timestamptz(3)

  @@index([createdAt], map: "idx_security_audit_events_created")
  @@index([actorUserId, createdAt], map: "idx_security_audit_events_actor")
  @@index([targetUserId, createdAt], map: "idx_security_audit_events_target")
  @@map("security_audit_events")
}
```

### 4.1 自定义迁移 SQL

```sql
/*
 * email 只保存规范值。
 *
 * 应用层 Email 值对象负责主要校验；数据库约束阻止绕过应用层
 * 写入包含前后空格或大写字符的第二种表示。
 */
ALTER TABLE users
  ADD CONSTRAINT ck_users_email_canonical
  CHECK (
    email = lower(btrim(email))
    AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    AND length(email) <= 320
  );

/*
 * Session 状态、撤销时间和原因必须保持一致。
 *
 * ACTIVE 不携带撤销元数据；REVOKED 必须同时包含时间和原因，
 * 避免依赖可空字段组合猜测业务状态；所有时间必须单调。
 */
ALTER TABLE auth_sessions
  ADD CONSTRAINT ck_auth_sessions_revocation
  CHECK (
    expires_at > created_at
    AND (
      (status = 'ACTIVE' AND revoked_at IS NULL AND revocation_reason IS NULL)
      OR
      (
        status = 'REVOKED'
        AND revoked_at IS NOT NULL
        AND revoked_at >= created_at
        AND revocation_reason IS NOT NULL
      )
    )
  );

/*
 * Token 状态与时间字段组成显式状态机。
 *
 * ACTIVE 没有终止时间；ROTATED 只有 rotated_at；
 * REVOKED 只有 revoked_at。
 */
ALTER TABLE refresh_tokens
  ADD CONSTRAINT ck_refresh_tokens_status
  CHECK (
    (status = 'ACTIVE' AND rotated_at IS NULL AND revoked_at IS NULL)
    OR
    (
      status = 'ROTATED'
      AND rotated_at IS NOT NULL
      AND rotated_at >= created_at
      AND revoked_at IS NULL
    )
    OR
    (
      status = 'REVOKED'
      AND rotated_at IS NULL
      AND revoked_at IS NOT NULL
      AND revoked_at >= created_at
    )
  );

/*
 * 审计动作使用固定列记录状态变化。
 *
 * USER_CREATED 记录创建后的角色与状态；角色/状态变更分别记录
 * 对应的前后值；Session 动作不允许夹带无关用户状态字段。
 */
ALTER TABLE security_audit_events
  ADD CONSTRAINT ck_security_audit_events_state_payload
  CHECK (
    (
      action = 'USER_CREATED'
      AND target_user_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NOT NULL
      AND previous_status IS NULL
      AND next_status IS NOT NULL
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'USER_ROLE_CHANGED'
      AND target_user_id IS NOT NULL
      AND previous_role IS NOT NULL
      AND next_role IS NOT NULL
      AND previous_role <> next_role
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'USER_STATUS_CHANGED'
      AND target_user_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NOT NULL
      AND next_status IS NOT NULL
      AND previous_status <> next_status
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'SESSION_CREATED'
      AND session_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'SESSION_REVOKED'
      AND session_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason IS NOT NULL
    )
    OR
    (
      action = 'REFRESH_REPLAY_DETECTED'
      AND session_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason = 'REFRESH_TOKEN_REPLAY'
    )
  );

/*
 * 每个 Session 只能有一个 ACTIVE Refresh Token。
 *
 * 轮换事务必须先把旧 token 改为 ROTATED，再插入新 ACTIVE token；
 * 数据库约束是并发错误的最终防线。
 */
CREATE UNIQUE INDEX uq_refresh_tokens_one_active_per_session
  ON refresh_tokens(session_id)
  WHERE status = 'ACTIVE';
```

### 4.2 删除与清理

- 本期不删除用户；`User → AuthSession` 使用 `Restrict`，禁止隐式跨聚合级联。
- 清理任务不属于 MVP 请求链路，但表设计必须支持按 `auth_sessions.expires_at` 批量清理。
- 后续清理 Job 可在保留安全调查窗口后删除已过期/撤销 Session，RefreshToken 随 Session 级联删除。
- `security_audit_events` 按独立保留策略清理，不随用户或 Session 删除。

---

## 5. 领域模型与状态流

### 5.1 值对象

| 值对象 | 不变量 |
| --- | --- |
| `UserId` / `SessionId` / `RefreshTokenId` | UUIDv7；由注入的 ID Generator 生成 |
| `Email` | trim + 小写；简单邮箱格式；长度 ≤320；API 与数据库只使用 canonical value |
| `PasswordHash` | Argon2id 编码结果；只允许基础设施 Hasher 创建或持久层 restore |
| `NewPassword` | 15–128 Unicode code point；NFC；不 trim、不改大小写 |
| `TokenHash` | SHA-256 digest 的 64 位小写十六进制；不接受原始 token |
| `UserRole` | 固定枚举；等级通过显式 `ROLE_RANK` 查询 |
| `PermissionCode` | 固定字符串联合类型；路由与角色映射共用同一事实来源 |

### 5.2 Account 状态

```text
ACTIVE ──disable──> DISABLED
DISABLED ──enable──> ACTIVE
```

- 禁止通过通用 `update` 修改状态。
- actor 不能修改自己的角色或状态。
- 禁用用户与吊销该用户全部活跃 Session 必须在同一 IAM 事务中完成。
- 重新启用用户不会恢复已撤销 Session，用户必须重新登录。
- 角色变化不撤销 Session；旧 access 最多保留 24 小时快照，下一次 refresh 使用数据库当前角色签发。
- 最后一个活跃 `SUPER_ADMIN` 不能被禁用或改为其他角色。

### 5.3 Session 与 RefreshToken 状态

```text
AuthSession:
ACTIVE ──logout──────────────> REVOKED(LOGOUT)
ACTIVE ──user disabled───────> REVOKED(USER_DISABLED)
ACTIVE ──confirmed replay────> REVOKED(REFRESH_TOKEN_REPLAY)
ACTIVE ──expiresAt <= now────> EXPIRED（由时间推导，不写伪状态）

RefreshToken:
ACTIVE ──successful rotate───> ROTATED
ACTIVE ──logout──────────────> REVOKED
ROTATED ──reuse in grace─────> STALE REQUEST（Session 不变）
ROTATED ──reuse after grace──> Session REVOKED
```

过期是时间条件，不是必须持久化的枚举状态；其余业务状态均显式存储。

### 5.4 稳定错误分类

- Domain Error：值对象或单聚合状态迁移非法。
- Application Error：资源不存在、凭证失败、授权拒绝、最后超管、并发陈旧请求。
- Infrastructure Error：数据库、随机源、密码库或 JWT 库异常；在适配器边界归一。
- HTTP 层只映射稳定错误，不让 NestJS/Prisma 异常进入 Domain。

---

## 6. 认证与会话流程

### 6.1 登录 `POST /v1/auth/login`

```text
请求 { email, password }
  → ValidationPipe → LoginRequestDto
  → Email.create() 得到 canonical email
  → RateLimiter.consume(ip, hash(email))
  → LoginUseCase
      → 事务外读取 CredentialSnapshot
          ├─ 不存在 → LoginAccountNotFoundError
          └─ 存在 → Argon2id verify（受全局并发信号量限制）
      → 密码不匹配 → InvalidCredentialsError
      → 密码匹配但用户已禁用 → UserDisabledError
      → 事务前生成 SessionId、RefreshTokenId、32-byte 随机值的无填充 base64url opaque token 与 tokenHash
      → IAM 短事务
          → 按 id 锁定 User 行并重新检查 ACTIVE
          → 创建绝对 expiresAt=now+7d 的 AuthSession
          → 插入 ACTIVE RefreshToken
          → 追加 SESSION_CREATED 安全审计
      → 事务提交后，根据事务返回的当前角色签发 access JWT
  → 200 { accessToken, tokenType, expiresIn, user }
  → Set-Cookie: refresh_token=<opaque>
```

规则：

- 密码校验和 Argon2 计算必须在数据库事务外。
- 登录事务重新读取并锁定 User，避免“校验密码后、创建 Session 前”与禁用操作发生竞态。
- 登录与禁用统一使用锁顺序 `users → auth_sessions → refresh_tokens`；要么先创建 Session 再被禁用事务吊销，要么先禁用再拒绝登录。
- JWT 签发在提交后进行。若极低概率签发失败，Session 保留但客户端拿不到凭证；该情况记录错误并允许重新登录，不做跨事务补丁。
- 不记录密码、明文 refresh、tokenHash、Authorization 或 Cookie。

### 6.2 刷新 `POST /v1/auth/refresh`

```text
请求 Cookie: refresh_token=<opaque>
  → RefreshSessionUseCase
      → tokenHash = sha256(cookie)
      → 事务前生成候选新 opaque token、id 与 hash
      → IAM 事务：lockRefreshContextByTokenHash(tokenHash)
          → 按 users → auth_sessions → refresh_tokens 稳定顺序加锁并重读
          ├─ token 不存在
          │    → INVALID
          ├─ User 为 DISABLED
          │    → Session 仍为 ACTIVE 时撤销为 USER_DISABLED 并审计
          │    → USER_DISABLED
          ├─ Session 为 REVOKED 或 expiresAt <= now
          │    → INVALID
          ├─ token 为 REVOKED
          │    → INVALID
          ├─ token 为 ROTATED 且 now-rotatedAt <= 5s
          │    → STALE（不改 Session、不写 Cookie）
          ├─ token 为 ROTATED 且超过 5s
          │    → 撤销 Session(REFRESH_TOKEN_REPLAY)
          │    → 写 REFRESH_REPLAY_DETECTED 审计
          │    → REPLAY
          └─ token 为 ACTIVE
               → 旧 token 改为 ROTATED(rotatedAt=now)
               → 插入新 ACTIVE token（同 Session）
               → 返回当前 User 角色与 Session 绝对过期时间
      → 事务提交
      → 根据封闭事务结果映射：
          VALID  → 签发 access；Set-Cookie 新 refresh
          STALE  → 409 REFRESH_TOKEN_STALE；不修改 Cookie
          REPLAY → 401 REFRESH_TOKEN_REPLAY；不修改 Cookie
          其他   → 对应 401；不修改 Cookie
```

关键约束：

- “吊销 Session 后返回重放错误”不能在 Prisma 事务回调里直接抛异常，否则吊销与审计会回滚。
- 事务回调只返回 `Valid | Stale | Replay | Invalid | UserDisabled` 封闭结果；应用层在事务成功提交后再抛稳定业务错误。
- 5 秒宽限仅处理多标签页、请求重试和响应乱序。宽限期内旧 token 不能换取新 token，只返回 409。
- 前端必须对 refresh 做单飞协调；收到 `REFRESH_TOKEN_STALE` 后等待共享 Cookie 更新，再最多重试一次。
- 对 stale/replay/invalid 响应禁止清空 Cookie，避免后到的错误响应覆盖先到的成功轮换结果。
- 新 token 沿用 Session 的绝对过期时间；轮换不延长 7 天 Session 生命周期。
- 新 Cookie 的 `Max-Age` 为 `session.expiresAt - now` 的剩余秒数。

### 6.3 登出 `POST /v1/auth/logout`

登出以 refresh Cookie 本身作为 Session 凭证，不要求仍然有效的 access token：

```text
请求 Cookie: refresh_token=<opaque>
  → @Public() 跳过 AccessTokenGuard 与 PermissionsGuard
  → LogoutUseCase
      → 缺失或未知 token：不报错
      → 已知 token 且 Session 未撤销：
          → 撤销 Session(LOGOUT)
          → 若 token 为 ACTIVE，则改为 REVOKED
          → 追加 SESSION_REVOKED 审计
  → 始终返回 204
  → 始终使用原 Cookie 属性清除 refresh_token
```

- 登出幂等，不能通过响应区分 Cookie 是否曾有效。
- 呈现任一属于该 Session 的已知 token 都可注销该 Session；登出不触发 replay 判定。
- 现有 access token 不查 Session，最多继续有效 24 小时，这是 `1.3` 已接受风险。

### 6.4 当前用户 `GET /v1/auth/me`

- 需要有效 access token。
- 读取数据库当前 User；不存在或 DISABLED 返回 401。
- 同时返回当前账户状态和 access token 的授权快照，禁止声称二者永远一致。

```json
{
  "data": {
    "user": {
      "id": "<uuid>",
      "email": "alice@apex.local",
      "role": "ADMIN",
      "status": "ACTIVE"
    },
    "authorization": {
      "tokenRole": "ADMIN",
      "permissions": ["user:read", "user:create"],
      "expiresAt": "2026-07-14T09:15:00.000Z",
      "stale": false
    }
  }
}
```

`authorization.stale` 在数据库当前角色与 token 角色不一致时为 `true`。前端可主动刷新，但服务端在旧 access 到期前仍按 token 快照授权。

### 6.5 access JWT claims

```jsonc
{
  "sub": "<user uuid>",
  "sid": "<auth session uuid>",
  "role": "ADMIN",
  "perms": [
    "user:read",
    "user:create",
    "user:status:change",
    "user:role:assign"
  ],
  "type": "access",
  "iss": "<JWT_ISSUER>",
  "aud": "<JWT_ACCESS_AUDIENCE>",
  "iat": 178...,
  "exp": 178...
}
```

- 算法固定为 `HS256`；严格校验 `iss/aud/exp/type/alg`。
- Header 不使用 `kid`；出现 `kid` 或非 `HS256` 算法均失败关闭。
- `sub/sid` 必须是 UUID；`role/perms` 必须属于代码白名单；权限数组有数量和重复值校验。
- `SUPER_ADMIN` 也必须先通过完整 JWT 验证，只在 `PermissionsGuard/UserPolicy` 授权阶段 bypass。

### 6.6 Refresh Cookie

| 属性 | 值 | 说明 |
| --- | --- | --- |
| 名称 | `refresh_token` | 固定 |
| `HttpOnly` | `true` | 禁止 JavaScript 读取 |
| `Secure` | 生产强制 `true` | 仅 HTTPS；本地开发可为 `false` |
| `SameSite` | `Lax` | 降低跨站请求风险，但不替代 Origin 校验 |
| `Path` | `/v1/auth` | 与实际版本化认证端点匹配 |
| `Domain` | 不设置 | Host-only，禁止扩大到父域 |
| `Max-Age` | Session 剩余秒数 | 绝对生命周期最长 604800 秒 |

设置和清除 Cookie 必须复用同一个 `RefreshCookieFactory`，确保 Path、Domain、SameSite、Secure 等属性完全一致，禁止 Controller 手工拼装。

---

## 7. RBAC 与对象级授权

### 7.1 权限码、角色等级与映射

```ts
/**
 * 权限码是路由声明与角色映射的唯一事实来源。
 *
 * 本期没有通用用户更新端点，因此不预留 user:update 等死权限。
 */
export const PermissionCode = {
  USER_READ: 'user:read',
  USER_CREATE: 'user:create',
  USER_STATUS_CHANGE: 'user:status:change',
  USER_ROLE_ASSIGN: 'user:role:assign',
} as const;

/**
 * 权限类型从唯一常量表推导。
 *
 * 禁止另写字符串联合类型，避免权限常量与类型定义发生漂移。
 */
export type PermissionCode =
  (typeof PermissionCode)[keyof typeof PermissionCode];

/**
 * 角色等级必须显式声明。
 *
 * 禁止依赖 TypeScript 或 PostgreSQL 枚举的声明顺序比较权限等级，
 * 新增角色时编译器必须要求同步更新本映射。
 */
export const ROLE_RANK: Record<UserRole, number> = {
  SUPER_ADMIN: 400,
  ADMIN: 300,
  OPERATOR: 200,
  VIEWER: 100,
};

/**
 * 所有具体权限组成 SUPER_ADMIN 的可序列化权限快照。
 *
 * 服务端授权仍按 SUPER_ADMIN 角色 bypass，但 JWT 的 perms
 * 始终保持字符串数组，避免同一字段出现两种运行时类型。
 */
export const ALL_PERMISSIONS: readonly PermissionCode[] =
  Object.values(PermissionCode);

/**
 * 固定角色到权限快照的映射。
 *
 * 每个角色都映射为具体数组；新增权限时必须同步权限矩阵测试。
 */
export const ROLE_PERMISSIONS: Record<UserRole, readonly PermissionCode[]> = {
  SUPER_ADMIN: ALL_PERMISSIONS,
  ADMIN: [
    PermissionCode.USER_READ,
    PermissionCode.USER_CREATE,
    PermissionCode.USER_STATUS_CHANGE,
    PermissionCode.USER_ROLE_ASSIGN,
  ],
  OPERATOR: [
    PermissionCode.USER_READ,
    PermissionCode.USER_CREATE,
  ],
  VIEWER: [
    PermissionCode.USER_READ,
  ],
};
```

安全含义：

- OPERATOR 可创建 VIEWER，但 `UserPolicy` 会拒绝创建 OPERATOR/ADMIN/SUPER_ADMIN。
- ADMIN 可创建和管理 OPERATOR/VIEWER。
- 只有 SUPER_ADMIN 可创建或赋予 SUPER_ADMIN。
- VIEWER 只读。

### 7.2 路由授权

- `AccessTokenGuard` 与 `PermissionsGuard` 都全局注册。
- `@Public()` 只跳过 access 身份认证与权限检查，不跳过 DTO 校验、限流或日志。
- `@RequirePermissions(...)` 默认要求全部权限；若未来出现“任一权限”语义，必须使用不同装饰器，禁止布尔参数魔法。
- 没有 `@RequirePermissions` 的受保护路由只要求认证；对象级授权仍由 Use Case 显式执行。

### 7.3 `UserPolicy`

```ts
/**
 * 用户管理策略只处理 actor、目标和期望状态之间的授权关系。
 *
 * 路由权限由 PermissionsGuard 处理；最后超管计数由事务用例处理。
 * 三者不能揉进一个巨型 can() 分支。
 */
export class UserPolicy {
  canCreate(actor: AuthenticatedActor, requestedRole: UserRole): boolean {
    if (actor.role === UserRole.SUPER_ADMIN) return true;
    return ROLE_RANK[requestedRole] < ROLE_RANK[actor.role];
  }

  canAssignRole(
    actor: AuthenticatedActor,
    target: UserAuthorizationSnapshot,
    nextRole: UserRole,
  ): boolean {
    if (actor.id === target.id) return false;
    if (actor.role === UserRole.SUPER_ADMIN) return true;

    return (
      ROLE_RANK[target.role] < ROLE_RANK[actor.role]
      && ROLE_RANK[nextRole] < ROLE_RANK[actor.role]
    );
  }

  canChangeStatus(
    actor: AuthenticatedActor,
    target: UserAuthorizationSnapshot,
  ): boolean {
    if (actor.id === target.id) return false;
    if (actor.role === UserRole.SUPER_ADMIN) return true;
    return ROLE_RANK[target.role] < ROLE_RANK[actor.role];
  }
}
```

### 7.4 最后一个活跃超管

- 改离 `SUPER_ADMIN` 或禁用活跃 SUPER_ADMIN 前，执行可序列化事务。
- 事务内读取 `COUNT(*) WHERE role='SUPER_ADMIN' AND status='ACTIVE'`。
- 若本次操作会让计数从 1 变为 0，返回 `LastSuperAdminError`。
- Serializable 冲突按 `12` 有界重试，每次重试必须重新读取并重新执行 Policy/不变量。
- 本期没有删除用户，因此不为删除路径预留代码。

### 7.5 用例授权顺序

1. 路由 Guard 校验粗粒度权限。
2. Use Case 读取目标快照。
3. `UserPolicy` 同时检查 target 和 requested next state。
4. 若涉及最后超管，进入可序列化事务重新读取并校验。
5. 聚合执行合法状态迁移。
6. 同一事务持久化用户、Session 吊销和安全审计。

---

## 8. Prisma 基础设施与显式事务

### 8.1 `DatabaseModule`

- 用显式 `DatabaseModule` 替代当前 `@Global() PrismaModule`。
- `DatabaseClient extends PrismaClient`，构造参数来自 Runtime 强类型配置。
- 业务模块显式 `imports: [DatabaseModule]`。
- 只有 Infrastructure Adapter 能注入 `DatabaseClient`。
- `onModuleInit` 连接；`onApplicationShutdown` 断开。
- 连接池、事务 `maxWait/timeout`、PostgreSQL statement/lock timeout 分别配置，禁止混成一个参数。

### 8.2 IAM Unit of Work

```ts
/**
 * IAM 事务上下文显式暴露绑定到同一个数据库事务的端口。
 *
 * Prisma TransactionClient 只存在于基础设施实现，
 * Application 不依赖 ORM 类型，也不通过全局变量获取当前事务。
 */
export interface IamTransaction {
  readonly users: UserRepository;
  readonly sessions: AuthSessionRepository;
  readonly refreshTokens: RefreshTokenRepository;
  readonly securityAudit: SecurityAuditRepository;
}

/**
 * 普通事务与可序列化事务使用不同的语义入口。
 *
 * Application 选择业务一致性语义，不传 Prisma 隔离级别字符串。
 */
export abstract class IamUnitOfWork {
  abstract run<T>(
    work: (transaction: IamTransaction) => Promise<T>,
  ): Promise<T>;

  abstract runSerializable<T>(
    work: (transaction: IamTransaction) => Promise<T>,
  ): Promise<T>;
}
```

`PrismaIamUnitOfWork` 在每次事务中用同一个 `tx` 构建全部 Repository。禁止以下实现：

- Repository 表面处于事务，内部却使用普通 Prisma Client。
- 把 `TransactionClient` 暴露给 Application 或 Domain。
- 使用可变单例、AsyncLocalStorage 或请求全局状态隐藏事务。
- 在事务中执行 Argon2、网络请求、日志传输或长时间计算。

### 8.3 专用锁定查询

Refresh 流程使用 Infrastructure 内部的 `lockRefreshContextByTokenHash`：

- 原始查询必须参数化。
- 稳定锁顺序为 `users → auth_sessions → refresh_tokens`。
- 返回 Application 端口定义的快照，不返回 Prisma record。
- 查询结果必须在获得锁后重新判定 token、Session 和 User 状态。
- 锁等待受 `lock_timeout` 约束；普通事务的锁超时/死锁整体回滚并映射 `CONCURRENT_MODIFICATION`，客户端可用原请求重试，服务端不隐藏无限重试。

### 8.4 Repository 与错误归一

- Mapper 集中处理 record ↔ Domain 转换。
- 读模型可直接投影为 Application Read Model，但 Prisma 类型不能外泄。
- `P2002` 的邮箱唯一冲突映射为 `UserEmailAlreadyUsedError`。
- 单 Session 多 ACTIVE token 的唯一索引冲突映射为内部并发错误并回滚，不对客户端暴露索引名。
- `P2034/40001/40P01` 只在可安全重放的可序列化事务外层有界重试。
- 未知数据库错误完整记录一次后映射通用 500；日志不包含 SQL 参数、密码或 token。

### 8.5 安全审计写入

- 用户创建、角色/状态变化、Session 创建/撤销、重放吊销与对应业务状态在同一事务提交。
- `SecurityAuditRepository` 为每种 action 提供类型化 append 方法，禁止暴露 `append(action, anyPayload)` 这类魔法入口。
- Repository 不提供 update/delete。
- Runtime 数据库角色对审计表只授予必要的 `INSERT`；查询由独立受控运维角色执行。
- 登录失败只记录集中脱敏的结构化安全日志与指标，避免攻击者通过失败请求无限写审计表。

---

## 9. HTTP API 契约

### 9.1 端点

| 方法 | 路径 | 路由权限 | 对象/状态规则 |
| --- | --- | --- | --- |
| POST | `/v1/auth/login` | `@Public()` + 限流 | 凭证与 User ACTIVE |
| POST | `/v1/auth/refresh` | `@Public()` | Session/Token 状态机 |
| POST | `/v1/auth/logout` | `@Public()` | refresh Cookie；幂等 |
| GET | `/v1/auth/me` | 已认证 | 当前 User 必须 ACTIVE |
| POST | `/v1/users` | `user:create` | `canCreate(actor, requestedRole)` |
| GET | `/v1/users` | `user:read` | 全局账号目录 |
| GET | `/v1/users/:id` | `user:read` | 全局账号目录 |
| PATCH | `/v1/users/:id/role` | `user:role:assign` | target + nextRole + 最后超管 |
| POST | `/v1/users/:id/disable` | `user:status:change` | target + 最后超管 + 吊销 Session |
| POST | `/v1/users/:id/enable` | `user:status:change` | target |

角色修改成功返回 200 和当前用户投影；请求角色与当前角色相同也返回 200，但不写变更审计。启用/禁用端点幂等返回 204；状态本已符合时不重复写审计，禁用路径仍确保不存在活跃 Session。

### 9.2 登录响应

```http
POST /v1/auth/login
Origin: https://admin.apex.local
Content-Type: application/json

{ "email": "Alice@Apex.Local", "password": "violet-cabin-echo-planet-4729" }
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
Set-Cookie: refresh_token=<opaque>; HttpOnly; Secure; SameSite=Lax; Path=/v1/auth; Max-Age=604800

{
  "data": {
    "accessToken": "eyJhbGci...",
    "tokenType": "Bearer",
    "expiresIn": 86400,
    "user": {
      "id": "<uuid>",
      "email": "alice@apex.local",
      "role": "ADMIN",
      "status": "ACTIVE"
    }
  }
}
```

### 9.3 创建用户

```http
POST /v1/users
Authorization: Bearer <access>
Content-Type: application/json

{
  "email": "bob@apex.local",
  "password": "maple-orbit-kiln-velvet-8306",
  "role": "OPERATOR"
}
```

- 成功返回 `201 Created` 与 `Location: /v1/users/<id>`。
- DTO 只验证结构；请求角色是否允许由 `UserPolicy.canCreate` 判断。
- 密码哈希在授权通过后、数据库事务前执行，避免无权请求消耗高成本哈希资源。

### 9.4 列表分页

- Keyset/Cursor：`orderBy: [{createdAt:'desc'},{id:'desc'}]`，读取 `pageSize+1`。
- `pageSize` 默认 20、上限 100。
- cursor 使用 base64url 编码并包含版本化结构 `{v:1,createdAt,id}`。
- cursor 解码后必须严格校验版本、时间和 UUID；禁止直接透传为 Prisma 参数。
- 响应 `{ data: [...], meta: { nextCursor, hasMore } }`。

### 9.5 成功与错误响应

- 单资源：`{ "data": { ... } }`。
- 集合：`{ "data": [...], "meta": { ... } }`。
- 无内容：`204`，不返回空信封。
- 错误：RFC 9457 Problem Details，`Content-Type: application/problem+json`。

```json
{
  "type": "https://apex.example.com/problems/invalid-credentials",
  "title": "密码错误",
  "status": 401,
  "code": "INVALID_CREDENTIALS",
  "traceId": "01JZZZZZZZZZZZZZZZZZZZZZZZ"
}
```

字段校验失败时才返回 `errors[]`。未知异常返回通用 500，不暴露 Prisma、SQL、路径或堆栈。

---

## 10. 配置与密钥

配置按进程入口拆分，禁止一个全局 schema 要求所有 Job 和 Runtime 都携带无关 Secret。

### 10.1 Runtime schema

| 变量 | 约束/默认 |
| --- | --- |
| `NODE_ENV` | `development/test/production` |
| `HTTP_HOST` / `PORT` | `0.0.0.0` / `3000` |
| `DATABASE_URL` | Runtime 最小权限连接串 |
| `JWT_ACCESS_SECRET_BASE64` | 严格 base64；解码后随机字节 ≥32 bytes |
| `JWT_ISSUER` | `apex-admin` |
| `JWT_ACCESS_AUDIENCE` | `apex-admin-web` |
| `JWT_ACCESS_TTL_SECONDS` | `86400` |
| `REFRESH_SESSION_TTL_SECONDS` | `604800` |
| `REFRESH_REUSE_GRACE_SECONDS` | `5`；范围 0–30 |
| `ARGON2_MEMORY_KIB` | `65536` |
| `ARGON2_TIME_COST` | `3` |
| `ARGON2_PARALLELISM` | `1` |
| `ARGON2_MAX_CONCURRENCY` | `4`；生产按容量压测调整 |
| `RATE_LIMIT_LOGIN_PER_IP` | `10` |
| `RATE_LIMIT_LOGIN_PER_EMAIL` | `5` |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` |
| `CORS_ORIGINS` | 明确 Origin 白名单 |
| `TRUST_PROXY_HOPS` | 默认 `0`；按受控代理拓扑显式配置 |
| `COOKIE_SECURE` | 生产必须为 `true` |

Runtime schema 不包含 `MIGRATION_DATABASE_URL`、`SUPER_ADMIN_EMAIL`、`SUPER_ADMIN_PASSWORD`，也不为 opaque refresh 配置无意义的签名密钥。

zod 还必须约束：

- access TTL 为 300–86400 秒，保证本规格承诺的最长收权窗口。
- Refresh Session TTL 为 86400–604800 秒，不允许轮换无限续期。
- 并发宽限为 1–30 秒；默认和生产基线为 5 秒。
- Argon2 参数不得低于当前采用的安全下限；生产参数变更必须重新容量压测。
- 所有限流阈值、并发数、端口和代理跳数都是有界正整数。

### 10.2 Migration schema

- 仅 Prisma CLI/发布 Migration Job 读取 `MIGRATION_DATABASE_URL`。
- Migration Role 有受控 DDL 权限，Runtime Role 没有。
- `prisma.config.ts` 只负责 CLI 数据源，不成为运行时配置入口。

### 10.3 Seed schema

`seed:super-admin` 只校验：

- `DATABASE_URL`：受控 bootstrap DML 连接。
- `SUPER_ADMIN_EMAIL`。
- `SUPER_ADMIN_PASSWORD`。
- Argon2 参数。

Seed 不要求 JWT、CORS、HTTP 端口或 Migration Secret。

### 10.4 JWT 密钥

- 算法最终确定为 `HS256`，不保留 RS256 分支。
- JWT Adapter 只读取 `JWT_ACCESS_SECRET_BASE64`，严格解码并校验字节长度；禁止把环境变量文本直接当作弱口令密钥。
- Secret 不提供不安全默认值，不写入仓库、镜像、日志或普通 ConfigMap。
- 单密钥模式不接受旧密钥 fallback。
- 换钥时协调停止旧实例、注入新密钥并启动全部实例；旧 access 立即失效，客户端可用未撤销 refresh 获取新 access。

---

## 11. 安全设计

### 11.1 密码建立与存储

- 密码作为单因素认证，长度为 15–128 Unicode code point。
- 接受打印 ASCII、空格和 Unicode；不要求大小写、数字或符号组合。
- 建立密码时执行 NFC 规范化；登录验证使用同一规范化规则。
- 禁止 trim、转小写或静默纠正密码。
- Application 的 `PasswordPolicy` 使用本地 `PasswordBlocklist` 拒绝常见、已泄露和上下文相关密码；Domain 的 `NewPassword` 只承担格式与规范化不变量，不依赖外部 Port。
- Argon2id 默认 `m=64MiB,t=3,p=1` 是项目配置，不称为 OWASP 固定基线；生产上线前必须按容器资源与并发压测。
- 使用 Argon2 库自带 verify；应用代码不自行比较哈希字节。
- 哈希编码必须保留算法和成本参数，以支持登录时识别并升级过低成本哈希。

参考：

- [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

### 11.2 登录限流与资源保护

- 进入高成本密码验证前，先消费 per-IP 与 per-email 限额。
- email key 使用 canonical email 的带服务端命名空间哈希，不在内存 key、指标标签或日志中暴露原文。
- 内存实现必须有 TTL 清理、最大 key 数和有界内存；不能使用永久增长 Map。
- 所有 Argon2 hash/verify（登录、创建账号、bootstrap）都必须占用 `ARGON2_MAX_CONCURRENCY` 信号量槽；HTTP 队列满返回 429，部署 Job 队列满则失败退出。
- 第 11 次同 IP 请求或第 6 次同 email 请求在 60 秒窗口内返回 429，并包含 `Retry-After`。
- 限流不依赖账号是否存在；成功请求也计入窗口，避免通过结果操纵计数。
- 部署在代理后时只信任明确跳数的受控代理；禁止直接相信任意 `X-Forwarded-For`。
- MVP 部署副本数必须为 1；多副本前先替换为 Redis 原子实现。

### 11.3 用户枚举

本系统选择内部运维可诊断性优先：

- 登录账号不存在：401 `LOGIN_ACCOUNT_NOT_FOUND`。
- 密码错误：401 `INVALID_CREDENTIALS`。
- 账号禁用：401 `USER_DISABLED`。
- 创建邮箱重复：409 `USER_EMAIL_ALREADY_USED`。

若登录、注册或找回端点开放公网，必须在新规格中改为统一外部失败语义；不能在本实现中保留两套动态模式。

### 11.4 Cookie 与 CSRF

- access 只走 Bearer Header，不进入 Cookie。
- refresh 只走 HttpOnly Host-only Cookie。
- `SameSite=Lax` 限制跨站请求携带 refresh Cookie，认证写端点不执行应用级 Origin 校验。
- CORS 使用精确白名单与 `credentials:true`，禁止 `*`。
- Cookie `Path` 只控制发送范围，不能被描述成同源脚本之间的安全隔离边界。
- 全站 HTTPS、HSTS、helmet。

### 11.5 access 失效边界

- Guard 不查数据库，因此 Session 撤销、用户禁用和角色降级不会立即让已签发 access 失效。
- 安全上限由 `JWT_ACCESS_TTL_SECONDS=86400` 保证。
- 需要立即收权的未来场景必须另行设计 token version/denylist 或在线会话校验；本期不加入静默查库 fallback。

### 11.6 安全审计

持久化审计至少记录：

- action、actorUserId、targetUserId、sessionId。
- 用户创建/角色/状态变化的类型化前后值。
- 类型化 revocationReason。
- correlationId；HTTP 使用 requestId/traceId，CLI 使用 Job 执行 ID。
- 服务端时间。

禁止记录密码、token、token hash、Cookie、Authorization Header 或完整请求体。安全审计与普通运行日志分离。

### 11.7 最小权限

- Runtime Role：业务 DML；对审计表仅追加。
- Migration Role：受控 DDL。
- 审计查询 Role：只读审计表，不拥有业务写权限。
- 数据库使用私网和 TLS，生产校验证书链与主机名。

---

## 12. 事务、并发与锁顺序

| 用例 | 事务策略 | 核心保证 |
| --- | --- | --- |
| 创建用户 | 普通短事务 | 友好查重 + DB 唯一约束兜底 + 同事务审计 |
| 登录 | 普通短事务 + User 行锁 | 重验 ACTIVE；Session、Token、审计原子创建 |
| Refresh | 普通短事务 + 显式行锁 | 锁后重读；Token 轮换或 Session 吊销原子提交 |
| Logout | 普通短事务 | Session 吊销、Token 状态、审计原子提交 |
| 改角色 | Serializable | Policy、最后超管、用户更新、审计 |
| 禁用 | Serializable | 最后超管、用户禁用、全部 Session 吊销、审计 |
| 启用 | 普通短事务 + User 行锁 | Policy、状态更新、审计 |

统一锁顺序：

```text
users → auth_sessions → refresh_tokens
```

规则：

- 任何同时访问这些表的写事务都遵守相同顺序。
- Refresh 的初始 hash 查询只用于定位；获得锁后必须重新读取全部状态。
- Serializable 重试仅覆盖 `P2034/40001/40P01`，最多 3 次，使用指数退避与抖动。
- 重试回调必须无事务外副作用；ID、随机 token 等可在事务外生成并在重试中复用。
- `P2002` 不重试，映射稳定业务冲突。
- 重放吊销事务返回封闭结果；HTTP 错误在提交后抛出。
- 所有事务设置 `maxWait`、`timeout`、`lock_timeout`，禁止无限等待。

---

## 13. Bootstrap：首个 SUPER_ADMIN

### 13.1 执行边界

- 独立部署 Job 执行 `pnpm seed:super-admin`。
- 不在应用每次启动时自动执行。
- 与 Migration Job 分离；先迁移，再 bootstrap，再启动 Runtime。
- CLI Adapter 调用 `BootstrapSuperAdminUseCase`，禁止脚本直接复制 Prisma upsert、邮箱规范化、密码策略或审计逻辑。

### 13.2 幂等与失败关闭

以 canonical `SUPER_ADMIN_EMAIL` 查询：

| 当前状态 | 结果 |
| --- | --- |
| 不存在 | 校验密码并创建 ACTIVE SUPER_ADMIN，写 `USER_CREATED` 审计 |
| 已存在且为 ACTIVE SUPER_ADMIN | 幂等成功，不修改密码 |
| 已存在但角色不是 SUPER_ADMIN | Job 失败，禁止静默跳过 |
| 已存在但状态是 DISABLED | Job 失败，禁止静默启用 |

流程：

1. Seed schema 校验环境变量。
2. `Email.create` 生成 canonical email。
3. `PasswordPolicy` 校验长度、NFC 和 blocklist。
4. 事务外执行 Argon2id hash。
5. 普通事务内重新读取邮箱：
   - 已存在则按上表判断。
   - 不存在则创建用户并追加安全审计。
6. 唯一冲突后重新读取并按同一状态表判断，禁止覆盖现有账号。

### 13.3 Secret 与输出

- Secret 只由部署环境注入，不进入镜像、仓库或日志。
- Job 失败不得回显密码、数据库连接串或 password hash。
- 成功输出只包含非敏感 userId、canonical email 和幂等/创建结果。
- 若当前开发 `.env` 中的远程凭证曾被共享或暴露，必须轮换；不能因为文件已被忽略就认为 Secret 未泄露。

---

## 14. 稳定错误码目录

| 场景 | HTTP | code |
| --- | --- | --- |
| DTO/字段校验失败 | 400 | `VALIDATION_FAILED` |
| access token 缺失、无效或过期 | 401 | `ACCESS_TOKEN_INVALID` |
| 登录账号不存在 | 401 | `LOGIN_ACCOUNT_NOT_FOUND` |
| 密码错误 | 401 | `INVALID_CREDENTIALS` |
| 用户已禁用 | 401 | `USER_DISABLED` |
| refresh 缺失、未知、过期或已撤销 | 401 | `REFRESH_TOKEN_INVALID` |
| refresh 确认重放且 Session 已吊销 | 401 | `REFRESH_TOKEN_REPLAY` |
| refresh 并发宽限期内的陈旧请求 | 409 | `REFRESH_TOKEN_STALE` |
| 路由或对象级授权拒绝 | 403 | `INSUFFICIENT_PRIVILEGE` |
| 管理端查询的用户不存在 | 404 | `USER_NOT_FOUND` |
| 邮箱已被占用 | 409 | `USER_EMAIL_ALREADY_USED` |
| 新密码命中 blocklist 或违反业务密码策略 | 422 | `PASSWORD_NOT_ALLOWED` |
| 试图移除最后一个活跃超管 | 409 | `LAST_SUPER_ADMIN` |
| 并发写入冲突且不能安全自动重试 | 409 | `CONCURRENT_MODIFICATION` |
| 登录限流或密码校验并发已满 | 429 | `RATE_LIMIT_EXCEEDED` |
| 未知服务端错误 | 500 | `INTERNAL_SERVER_ERROR` |

要求：

- 登录账号不存在只使用 `LOGIN_ACCOUNT_NOT_FOUND`，不再与 `INVALID_CREDENTIALS` 混用。
- 管理端资源不存在使用 `USER_NOT_FOUND`，与登录语义分离。
- Logout 无论 token 是否有效都返回 204，不产生 token 有效性错误。
- 代码中的错误目录、OpenAPI 与本表由 CI 一致性测试约束。

---

## 15. 测试策略

测试按风险分层，不以省略单元测试来换取表面上的 E2E 覆盖。

### 15.1 Domain 单元测试

- `Email`：trim、小写、非法格式、最大长度、restore。
- `NewPassword`：15/128 边界、Unicode code point、NFC、保留空格、不 trim。
- `AuthSession`：logout、禁用、重放撤销、重复撤销幂等或拒绝语义。
- `RefreshToken`：ACTIVE→ROTATED、ACTIVE→REVOKED、非法迁移。
- `ROLE_RANK/ROLE_PERMISSIONS`：所有角色穷尽且无未知权限码。
- `UserPolicy` 使用完整表驱动矩阵覆盖：
  - actor 四种角色。
  - target 四种角色。
  - nextRole 四种角色。
  - 自操作。
  - 创建、改角色、启停三类动作。

Domain 测试不启动 NestJS、不连接数据库。

### 15.2 Application 单元测试

- 创建用户：先授权，再校验/哈希，再进入 UoW。
- 登录：不存在、密码错、禁用、事务内重验状态、签发失败。
- Refresh 对 `Valid/Stale/Replay/Invalid/UserDisabled` 每个封闭结果的映射。
- 验证 replay 业务错误在 UoW 成功返回后抛出，而不是在事务回调内抛出。
- Logout 缺失/未知 token 仍返回成功。
- 角色/状态用例始终调用 Policy，不能只依赖路由 Guard。
- Bootstrap 四种现有账号状态。
- Clock、ID、随机 token、Hasher、Limiter 使用 Fake/Stub，不依赖真实时间或随机性。

### 15.3 Repository/事务集成测试

使用 Testcontainers 或 CI Service Container，PostgreSQL 主版本、时区、排序规则和角色权限与生产一致，禁止 SQLite。

- 从空库执行完整 `prisma migrate deploy`。
- canonical email CHECK 与唯一约束。
- Session 撤销 CHECK。
- Token 状态 CHECK。
- 每 Session 单 ACTIVE token 部分唯一索引。
- Mapper 往返。
- 登录与禁用并发：不存在“禁用后新建活跃 Session”的结果。
- 并发刷新同一个 ACTIVE token：
  - 第一请求成功轮换。
  - 5 秒内第二请求得到 STALE，Session 保持有效。
  - 宽限期外旧 token 重用，Session 被持久化吊销。
- HTTP 返回 replay 错误后重新查库，确认吊销和审计已经提交。
- Logout 与轮换竞争符合稳定锁顺序，无未处理死锁。
- 两个请求并发禁用/降级最后超管，恰好一个成功或两者都保留至少一个活跃超管。
- 用户禁用、Session 批量吊销和审计同事务回滚/提交。
- 使用真实 Runtime Role 验证权限，不用 PostgreSQL 超级用户掩盖缺失 GRANT。

### 15.4 HTTP E2E

- 登录成功：200、access、`Path=/v1/auth` 的正确 Set-Cookie。
- 登录不存在/密码错/禁用分别返回唯一确定的错误码。
- 非法或缺失 Origin 在生产配置下返回 403。
- `@Public()` 只跳过 access Guard，不跳过 Origin、DTO 与限流。
- Refresh 成功、STALE、REPLAY、INVALID 的状态码、Cookie 副作用和 Problem Details。
- 登出无需 access，始终 204，并用完全相同属性清 Cookie。
- access 过期后可 refresh；Session 绝对过期后不可 refresh。
- 未认证受保护端点返回 401。
- OPERATOR 创建 VIEWER 成功，创建 OPERATOR/ADMIN/SUPER_ADMIN 均为 403。
- ADMIN 不能把目标提升到 ADMIN/SUPER_ADMIN，不能操作同级或更高级目标。
- 任何 actor 都不能修改自己的角色或状态。
- 非超管不能操作超管；最后超管保护。
- 禁用用户后 refresh 稳定返回 `USER_DISABLED`，且 Session 已吊销。
- `/me` 正确报告 token 授权快照是否 stale。
- 重复邮箱 409；限流 429 + `Retry-After`。
- Problem Details、traceId、未知字段拒绝、成功信封。

### 15.5 架构测试

CI 使用只读测试扫描 import：

- Domain 不导入 NestJS、Prisma、Presentation、Infrastructure。
- Application 不导入 Prisma 或 HTTP DTO。
- Presentation 不导入 Prisma Adapter。
- `modules/iam` 外部只能从 `public-api.ts` 导入 IAM 契约。
- `DatabaseClient` 只允许 Infrastructure 和 Composition Root 导入。
- 禁止 `forwardRef()`；出现循环依赖直接失败。

### 15.6 测试数据与性能

- Builder/Factory 创建最小场景，不复制生产个人数据。
- 每个 Worker 使用独立数据库/Schema，不共享状态。
- 测试环境可以降低 Argon2 参数，但必须保留同一配置解析、Hasher 和编码路径。
- 生产发布前用真实 Argon2 参数压测延迟、RSS、线程池和最大并发，确定 `ARGON2_MAX_CONCURRENCY`。

---

## 16. 当前工程前置改造（P0）

当前仓库仍是脚手架，业务实现前先建立边界：

1. **TypeScript 严格模式**
   - 开启 `strict`、`noImplicitAny`、`strictBindCallApply`、`noFallthroughCasesInSwitch`、`noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`。
2. **Secret 治理**
   - 当前 `.env` 已被 `.gitignore` 忽略且未被 Git 跟踪，不执行无效的 `git rm --cached .env`。
   - 文件包含远程连接形态的敏感值；若凭证曾共享或暴露，立即轮换。
   - 只提交不含真实值的 `.env.example`。
3. **Prisma CLI 配置**
   - `prisma.config.ts` 移除 Runtime `DATABASE_URL` 读取，改为 Migration schema 的 `MIGRATION_DATABASE_URL`。
4. **DatabaseModule**
   - 移除 `@Global() PrismaModule` 和业务代码中的直接 `process.env`。
   - 建立配置驱动的 `DatabaseClient`。
5. **分入口配置**
   - 引入 `@nestjs/config` + zod。
   - Runtime、Migration、Seed 分别校验。
6. **稳定启动入口**
   - 保留 `src/main.ts` 作为 Nest 默认 entry file，只调用 `bootstrap()`。
   - `bootstrap/bootstrap.ts` 与 `setup-application.ts` 承担应用组装，`start:prod` 仍可稳定指向 `dist/main`。
7. **全局 HTTP 能力**
   - helmet、精确 CORS、URI versioning、严格 ValidationPipe、请求 ID、Problem Details、成功信封、健康检查和 shutdown hooks。
8. **依赖与脚本**
   - 增加 JWT、Argon2、Cookie、zod、Testcontainers 等本规格真实需要的依赖。
   - 增加 `prisma:generate`、`prisma:migrate:deploy`、`seed:super-admin`。
9. **只读质量门禁**
   - 将 `lint` 拆为不修改文件的 `lint:check` 与显式 `lint:fix`。
   - CI 执行 typecheck、lint:check、单元、集成、E2E、架构测试和迁移验证。

---

## 17. 未引入能力与取舍

### 17.1 不引入 Transactional Outbox

本期没有邮件、消息或第三方副作用。安全审计是同库事务数据，不通过 Outbox 异步写入。新增异步副作用时单独设计 Outbox，不提前创建空表或占位 Port。

### 17.2 最小审计而非完整审计平台

本期交付不可缺失的安全状态审计，但不包含 WORM、签名链、导出和长期归档。进入合规环境前另立规格。

### 17.3 Refresh 并发宽限

5 秒宽限降低合法多标签页导致整段 Session 被吊销的概率，代价是宽限期内的旧 token 使用不会立即触发家族吊销。旧 token 在宽限期内仍不能获得新 token；这是明确、可测试的安全与可用性取舍。

### 17.4 Access 快照

不查库的 access Guard 保持低耦合和稳定延迟，但收权不是立即生效。若未来业务要求秒级收权，必须选择在线 Session 校验或 token version，不在现有 Guard 中加入条件查库 fallback。

### 17.5 单副本限流

内存 RateLimiter 是单副本部署约束，不是可扩展实现。需要多副本时直接替换为 Redis Port Adapter，并删除内存生产实现；不长期保留双实现动态 fallback。

### 17.6 MFA

MFA 不在本期实现，但公网或高敏生产发布必须先完成独立 MFA/step-up 规格。不得把“内部系统”永久当作免除 MFA 的理由。

---

## 18. 交付里程碑

1. **P0 工程边界**
   - 严格 TypeScript、Secret 治理、分入口配置、DatabaseModule、bootstrap、HTTP 基础设施、只读 CI。
2. **IAM 数据层**
   - User/AuthSession/RefreshToken/SecurityAuditEvent、枚举、CHECK、部分唯一索引、首个迁移。
3. **账户与授权**
   - 值对象、Account 聚合、权限映射、UserPolicy、账户 Repository/UoW、创建/查询/角色/状态用例。
4. **会话与认证**
   - Session/Token 状态机、JWT、opaque token、Cookie Factory、限流、登录/刷新/登出/me。
5. **HTTP 安全**
   - 三个全局 Guard、装饰器、DTO、Problem Details、Origin/CORS、日志脱敏。
6. **Bootstrap 与审计**
   - `BootstrapSuperAdminUseCase`、CLI Adapter、数据库权限与审计查询边界。
7. **测试**
   - Domain/Application 单元、真实 PG 集成、HTTP E2E、架构测试、Argon2 容量压测。
8. **文档**
   - 模块 README、OpenAPI、错误码目录、部署变量模板、密钥轮换与故障处置 Runbook。

每个里程碑完成后检查：

- 是否引入跨层或跨子域反向依赖。
- 是否产生可空字段组合、隐式事务或隐藏请求状态。
- 是否重复实现权限、Cookie、错误映射或密码策略。
- 是否存在仅靠代码约定而没有测试/数据库约束的不变量。
- 是否降低后续人员与 AI 对数据流、状态流和拒绝路径的推导能力。

---

## 19. 固定决策与演进触发条件

本规格没有阻塞实现的未决事项。以下为固定 MVP 决策：

| 事项 | 当前固定决策 | 重新评估触发条件 |
| --- | --- | --- |
| JWT | HS256、单密钥、无 kid | 多验证方、独立认证服务或不停机轮换 |
| Cookie | Host-only、`Path=/v1/auth` | API 域名或反向代理路径发生正式变更 |
| Session | 绝对 7 天、轮换不续期 | 产品需要长期“保持登录”并完成风险评估 |
| Refresh 宽限 | 5 秒 | 真实客户端并发数据或威胁模型变化 |
| 角色权限 | `7.1` 固定映射 | 产品批准新增业务动作；必须同步策略与矩阵测试 |
| 登录阈值 | IP 10、email 5、60 秒 | 压测或运行指标支持调整 |
| 限流后端 | 单副本内存 | 应用副本数大于 1 |
| 错误语义 | 内部系统精确错误 | 任一认证相关端点开放公网 |
| MFA | 本期不实现 | 公网或高敏生产发布 |
| 多租户 | 不支持 | 产品正式转为 SaaS |

阈值型配置允许在约束范围内调优，不得通过环境变量切换两套架构、鉴权语义或兼容逻辑。
