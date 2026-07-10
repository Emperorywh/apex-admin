# NestJS + Prisma + PostgreSQL 开发最佳实践

> 文档状态：架构与工程基线  
> 适用场景：全新 API 系统、模块化单体优先、允许破坏式重构、不承担 legacy 兼容  
> 当前工程基线：Node.js 22、NestJS 11、Prisma ORM 7.8、`@prisma/adapter-pg`、PostgreSQL  
> 更新日期：2026-07-10

## 阅读导航

- 架构负责人：重点阅读 [总体架构](#3-总体架构)、[请求与状态流](#4-请求流数据流与状态流)、[Domain / Application](#7-domainapplication-与依赖注入)、[AI-Friendly Architecture](#19-ai-friendly-architecture)。
- 后端开发：重点阅读 [Prisma 7 基础设施](#8-prisma-7-基础设施)、[PostgreSQL 建模](#9-postgresql-数据建模)、[查询与性能](#10-查询与性能)、[事务与并发](#11-事务并发与可靠副作用)。
- DBA / 发布负责人：重点阅读 [数据库发布](#12-prisma-migrate-与数据库发布)、[可观测性](#17-日志指标追踪与健康检查)、[容器与灾备](#18-容器部署与灾难恢复)。
- 测试 / 安全负责人：重点阅读 [安全设计](#13-安全设计)、[测试策略](#15-测试策略)、[代码质量与 CI](#16-代码质量与-ci)、[检查清单](#21-检查清单)。
- 当前仓库实施：直接查看 [当前 `api` 工程落地顺序](#22-当前-api-工程的建议落地顺序)。

第 1～21 章是通用基线，第 22 章是当前仓库的阶段性实施附录；仓库状态变化时只更新附录，不反向改变通用架构原则。

## 1. 文档目标

本文给出一套可长期演进的 NestJS + Prisma + PostgreSQL 工程基线。目标不是堆叠框架功能，而是让系统满足以下要求：

- 高内聚、低耦合，业务模块拥有明确边界和数据所有权。
- 业务规则不依赖 HTTP、Prisma 或 PostgreSQL 的实现细节。
- 请求流、状态流、事务边界和副作用均显式可追踪。
- 数据正确性由领域规则与数据库约束共同保证。
- 代码可维护、可扩展、可复用、可测试，也便于 AI 快速推导。
- 不保留 legacy、灰度、fallback、deprecated 或重复实现。

本文使用以下约束词：

- **必须**：项目统一遵守的架构基线。
- **应该**：默认采用；只有明确、可记录的理由才能偏离。
- **可以**：按业务规模和风险选择。

### 1.1 版本策略

- Node.js 使用仍受支持的 LTS；容器和 CI 固定明确版本，不能使用漂移的 `latest`。
- NestJS、Prisma CLI 与 Prisma Client 固定兼容版本，CLI 与 Client 必须保持一致。
- PostgreSQL 使用官方仍支持的稳定主版本；本地、CI、预发布和生产保持相同主版本、扩展、排序规则与时区配置。
- 当前仓库以 Node.js 22、NestJS 11、Prisma 7.8 为实际基线；升级先通过迁移、集成、E2E 和性能测试，再直接替换旧路径。
- 包管理统一使用 pnpm 和已提交的 Lockfile，CI 使用冻结 Lockfile 安装。

## 2. 核心结论

1. 默认采用**按业务领域拆分的模块化单体**，不要一开始就拆微服务。
2. Controller 只负责 HTTP 协议适配；一个应用用例负责一个清晰业务动作。
3. Domain 不依赖 NestJS、Prisma、HTTP 和环境配置；Application 不依赖 Prisma 具体实现。
4. Prisma Client 只允许出现在基础设施持久化层，不作为领域模型或响应 DTO。
5. 每张表有且只有一个业务模块拥有；跨模块通过公开应用接口或事件协作。
6. 数据库的唯一约束、外键、非空约束和检查约束是并发场景下的最终防线。
7. 事务边界属于应用用例；事务内禁止调用第三方 API、发送消息或进行长计算。
8. 生产只执行已审查的 `prisma migrate deploy`，不使用 `db push`，不由应用副本自动迁移。
9. Prisma 集成测试和 E2E 必须使用真实 PostgreSQL，不能用 SQLite 替代。
10. 日志、指标、追踪、健康检查、备份恢复与迁移策略属于上线基线，不是事后补充。

## 3. 总体架构

### 3.1 首选模块化单体

对于全新后台系统，首选单进程、单仓库、单 PostgreSQL 实例下的模块化单体：

- 部署和事务模型简单。
- 模块边界可以先在代码和数据所有权上验证。
- 避免过早引入分布式事务、消息可靠性、服务发现和跨服务调试。
- 当某个模块出现独立扩缩容、隔离故障或团队自治需求时，再依据稳定边界拆分服务。

“单体”不等于“所有代码互相访问”。即使表都在同一数据库中，也必须遵守模块所有权。

### 3.2 分层与依赖方向

运行时调用流与源码依赖方向是两件事，必须分开理解：

```text
运行时调用：
HTTP → Presentation → Application → Domain
                           ↓ 调用 Application 定义的 Port
                  Infrastructure Adapter → PostgreSQL / 外部系统

源码依赖：
Presentation ─────→ Application ─────→ Domain
Infrastructure ───→ Application / Domain
Composition Root ─→ Presentation / Application / Infrastructure
```

Application 在运行时调用 Repository Port，NestJS 组装点把该 Port 绑定到 Infrastructure Adapter；Application 源码本身绝不导入 Adapter。

依赖规则：

- `domain` 必须是纯 TypeScript，不导入 NestJS、Prisma、HTTP、配置或日志实现。
- `application` 依赖 Domain 和抽象 Port；不得导入生成的 Prisma 类型。
- `infrastructure` 实现 Application 定义的 Port，并完成 Prisma 与领域模型之间的映射。
- `presentation` 负责协议转换，不实现领域规则，不创建事务，不拼装 Prisma 查询。
- `*.module.ts` 只负责依赖组装，不承载业务逻辑。
- 源码依赖只能指向内层抽象；禁止通过 `forwardRef()` 长期掩盖循环依赖。

### 3.3 推荐目录

```text
src/
├─ bootstrap/
│  ├─ bootstrap.ts
│  └─ setup-application.ts
├─ platform/
│  ├─ config/
│  ├─ database/
│  ├─ logging/
│  ├─ observability/
│  └─ security/
├─ modules/
│  └─ users/
│     ├─ users.module.ts
│     ├─ public-api.ts
│     ├─ presentation/
│     │  └─ http/
│     │     ├─ users.controller.ts
│     │     ├─ dto/
│     │     └─ presenters/
│     ├─ application/
│     │  ├─ commands/
│     │  ├─ queries/
│     │  ├─ use-cases/
│     │  └─ ports/
│     ├─ domain/
│     │  ├─ entities/
│     │  ├─ value-objects/
│     │  ├─ policies/
│     │  ├─ events/
│     │  └─ errors/
│     └─ infrastructure/
│        └─ persistence/
│           └─ prisma/
└─ shared/
   └─ kernel/
```

`shared` 只放真正无业务归属且稳定的基础抽象，例如 `Clock`、`IdGenerator`、分页值对象。禁止把无法归类的代码都放入 `SharedModule`。

### 3.4 模块边界与数据所有权

每个模块必须明确记录：

- 职责和不负责的事项。
- 拥有的表、领域不变量和状态机。
- 对外公开的 Facade、Command、Query 或事件。
- 允许依赖的模块。

跨模块规则：

- 只能从对方的 `public-api.ts` 导入公开契约。
- 禁止直接注入另一个模块的 Repository。
- 禁止直接查询或写入另一个模块拥有的表。
- 禁止通过 Prisma `include` 跨越业务边界构造巨型对象图。
- 需要立即返回结果的同步协作调用对方公开应用门面；最终一致协作使用明确的集成事件。
- 同步门面调用不等于共享数据库事务，两个模块各自提交后仍可能出现部分成功。
- 真正需要原子不变量的数据应归入同一一致性边界；出现跨模块原子需求时，优先重新划分模块，而不是借用对方 Repository。
- 边界确认独立后，跨模块流程使用 Saga / Process Manager、Outbox 和明确补偿，不假设数据库原子提交。
- 若两个模块频繁互查内部数据，应重新评估边界，而不是继续增加例外。

### 3.5 NestJS 扩展点职责

| 扩展点 | 单一职责 |
| --- | --- |
| Middleware | 请求 ID、原始 HTTP 上下文初始化 |
| Guard | 身份认证、粗粒度访问策略判断 |
| Pipe | 输入解析、显式类型转换、结构验证 |
| Interceptor | 日志上下文、耗时统计、响应序列化 |
| Exception Filter | 将内部错误映射为稳定 HTTP 错误 |
| Decorator | 提取当前用户、租户、请求上下文 |
| Controller | DTO 与 Command/Query 转换，调用用例 |

同一逻辑不得同时散落在 Middleware、Guard、Interceptor 和 Controller 中。

Provider 默认是单例。禁止把当前用户、租户、请求对象或事务客户端写入单例成员变量。请求关联信息可以集中存入 `AsyncLocalStorage`，但权限判定所需的 `actorId`、`tenantId` 等关键参数仍应显式传给用例。

## 4. 请求流、数据流与状态流

### 4.1 写请求

```text
HTTP Body / Params
  → ValidationPipe
  → Request DTO
  → Controller 映射 Command
  → Use Case 开启事务并编排
  → Domain 校验不变量、产生状态和事件
  → Repository Port
  → Prisma Adapter 映射并持久化
  → PostgreSQL 约束兜底
  → 提交事务 / 写入 Outbox
  → Presenter 映射 Response DTO
```

### 4.2 读请求

```text
HTTP Query
  → 严格验证后的 Query DTO
  → Query Use Case
  → Read Repository / Prisma Projection
  → 只选择需要字段
  → Response DTO + Pagination Meta
```

读模型不必强制还原完整聚合。复杂列表可以由独立 Read Repository 直接投影为应用层读模型，但 Prisma 类型仍不得越过基础设施边界。

### 4.3 状态归属

- 业务持久状态：PostgreSQL 是唯一事实来源。
- 单次请求上下文：显式参数或受控的请求上下文容器。
- 跨请求进程内状态：默认禁止；多副本下会产生不一致。
- 缓存：是可丢失的派生数据，必须定义 key、TTL、失效和一致性策略。
- 定时任务和消费者状态：持久化游标、租约或作业记录，禁止只保存在内存。
- 配置：启动时加载并验证，运行期只读；动态业务配置应建模为领域数据。

## 5. 启动、配置与进程生命周期

### 5.1 配置基线

- `process.env` 只能在配置层读取。
- 启动时一次性校验所有必需变量，非法配置直接失败。
- 应用层注入经过解析的强类型配置，不读取字符串键。
- 生产密钥不得提供不安全默认值，不写入仓库、镜像或日志。
- Prisma CLI 只在 `prisma.config.ts` 读取必填直连 `MIGRATION_DATABASE_URL`；运行时 `DATABASE_URL` 由 NestJS 配置层管理。两者职责独立，不使用静默 fallback。
- 本地 `.env` 只用于开发，提交 `.env.example` 说明字段而不包含真实值。

### 5.2 应用启动示例

```ts
/**
 * 在唯一启动入口统一安装跨模块基础能力。
 *
 * 这里不放业务条件，确保所有 HTTP 请求经过同一套
 * 校验、安全、版本和关闭流程。
 */
async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule, {
    bufferLogs: true,
  });

  app.enableShutdownHooks();
  app.enableVersioning({ type: VersioningType.URI });
  app.use(helmet());
  app.enableCors(buildCorsOptions());
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      transformOptions: {
        enableImplicitConversion: false,
      },
    }),
  );

  const config = app.get(AppConfig);
  await app.listen(config.http.port, config.http.host);
}

/**
 * 启动失败必须交给统一日志出口并以非零状态退出。
 *
 * 禁止吞掉异常后让容器维持“存活但不可用”的状态。
 */
void bootstrap().catch(handleFatalStartupError);
```

注意：

- `transform: true` 不等于允许隐式转换。数字、布尔、日期应由 DTO 明确转换。
- CORS 必须使用明确来源白名单，不能在携带凭证时配置任意来源。
- 应设置 HTTP 请求体、上传文件、分页大小和查询复杂度上限。
- 生产环境应使用结构化 Logger 替换默认日志实现。

## 6. API 契约、DTO 与错误模型

### 6.1 边界模型必须分离

```text
HTTP Request DTO
  → Application Command / Query
  → Domain Entity / Value Object
  → Prisma Record

Prisma Projection
  → Application Read Model
  → HTTP Response DTO
```

禁止复用：

- 不用 Prisma Model 作为 DTO。
- 不用 Request DTO 作为领域实体。
- 不把数据库字段原样透传给客户端。
- 不依赖序列化装饰器“碰巧隐藏”密码哈希、内部标记等字段。

### 6.2 DTO 校验边界

DTO 负责协议层的结构和格式：

- 必填、长度、格式、枚举、数组数量。
- 分页上限、排序字段白名单。
- 嵌套对象和显式类型转换。

Application / Domain 负责业务规则：

- 唯一性、资源所有权、权限。
- 状态迁移、额度、不变量。
- 跨记录或跨聚合约束。

```ts
/**
 * DTO 只声明客户端允许提交的协议字段。
 *
 * 邮箱是否已被占用属于业务与并发规则，
 * 不应通过自定义 Validator 查询数据库。
 */
export class CreateUserRequestDto {
  @IsEmail()
  @MaxLength(320)
  email!: string;

  @IsString()
  @Length(12, 128)
  password!: string;
}
```

`PATCH` DTO 不应无条件用 `PartialType()` 暴露全部字段。应按具体业务动作建立 `ChangeEmailRequestDto`、`DisableUserRequestDto` 等明确契约。

### 6.3 HTTP 语义

- 创建成功：`201 Created`，必要时返回 `Location`。
- 查询成功：`200 OK`。
- 无响应体成功：`204 No Content`。
- 输入结构错误：`400 Bad Request`。
- 未认证：`401 Unauthorized`。
- 已认证但无权限：`403 Forbidden`。
- 不存在：`404 Not Found`。
- 唯一约束、幂等键或版本冲突：`409 Conflict`。
- 合法格式但违反领域状态：`422 Unprocessable Entity`。
- 未知服务端错误：`500 Internal Server Error`。

不得用 `200` 包装失败，也不得把所有业务失败都映射为 `400`。

### 6.4 稳定错误响应

建议采用 Problem Details 风格，并扩展稳定业务错误码：

```json
{
  "type": "https://example.com/problems/user-email-already-used",
  "title": "邮箱已被使用",
  "status": 409,
  "code": "USER_EMAIL_ALREADY_USED",
  "traceId": "01JZZZZZZZZZZZZZZZZZZZZZZZ",
  "errors": []
}
```

Problem Details 响应使用 `Content-Type: application/problem+json`。`errors` 是本项目的可选扩展字段，只在字段级校验失败时出现，并使用稳定字段路径和错误码。

错误分层：

- Domain Error：领域不变量或状态迁移失败。
- Application Error：不存在、授权失败、并发冲突等用例失败。
- Infrastructure Error：数据库、消息、第三方系统失败。
- HTTP Error：全局 Filter 对内部错误的协议映射。

Domain 不抛 `BadRequestException` 等 NestJS 异常。Prisma 的错误码、SQL、表名、约束名和堆栈不得返回客户端；已知 Prisma 错误在持久化边界转换为稳定应用错误，未知错误记录一次完整日志后返回通用 `500`。

### 6.5 成功响应

选择一种全局契约并保持一致。推荐：

- 单资源：`{ "data": { ... } }`
- 集合：`{ "data": [ ... ], "meta": { ... } }`
- 无内容：直接 `204`，不返回空 envelope。

不要同时保留 envelope 与裸对象两套长期格式。

## 7. Domain、Application 与依赖注入

### 7.1 用例单一职责

一个用例类只代表一个业务动作，例如：

- `CreateUserUseCase`
- `ChangeUserEmailUseCase`
- `DisableUserUseCase`
- `GetUserDetailQuery`

不要创建包含几十个 CRUD、鉴权、事务和通知方法的巨型 `UsersService`。简单项目无需默认引入完整 CQRS 框架；“一用例一类”已足够明确。只有读写模型、事件中间件或分布式协作确实复杂时，再引入 `@nestjs/cqrs`。

### 7.2 Repository Port

```ts
/**
 * 仓储端口表达应用层真正需要的持久化能力。
 *
 * 方法使用领域类型，不能泄漏 Prisma 的 WhereInput、
 * Select、TransactionClient 或生成模型。
 *
 * 示例把用户身份建模为平台级全局账号；如果产品采用
 * “租户内账号”，所有条件和唯一约束都必须加入 TenantId。
 */
export abstract class UserRepository {
  abstract findByNormalizedEmail(email: string): Promise<User | null>;

  abstract add(user: User): Promise<void>;
}

/**
 * 事务上下文显式提供绑定到同一事务的仓储。
 *
 * 这样既不会把 Prisma TransactionClient 泄漏到应用层，
 * 也不会通过全局变量隐藏当前事务。
 */
export interface UsersTransaction {
  readonly users: UserRepository;
  readonly outbox: OutboxRepository;
}

/**
 * 用户模块的工作单元只公开本模块需要的事务资源。
 *
 * 它不暴露 ORM 客户端，也不承担跨模块的数据访问。
 */
export abstract class UsersUnitOfWork {
  abstract run<T>(
    work: (transaction: UsersTransaction) => Promise<T>,
  ): Promise<T>;

  abstract runSerializable<T>(
    work: (transaction: UsersTransaction) => Promise<T>,
  ): Promise<T>;
}

/**
 * 用例负责编排，不负责 HTTP 映射或 SQL 查询细节。
 *
 * “先查询”用于产生友好错误，数据库唯一约束仍是
 * 并发条件下保证邮箱唯一的最终防线。
 */
@Injectable()
export class CreateUserUseCase {
  constructor(
    private readonly passwordHasher: PasswordHasher,
    private readonly userIdGenerator: UserIdGenerator,
    private readonly unitOfWork: UsersUnitOfWork,
  ) {}

  async execute(command: CreateUserCommand): Promise<CreateUserResult> {
    const email = Email.create(command.email);
    const passwordHash = await this.passwordHasher.hash(command.password);
    const user = User.create({
      id: this.userIdGenerator.next(),
      email,
      passwordHash,
    });
    const events = user.pullDomainEvents();

    return this.unitOfWork.run(async ({ users, outbox }) => {
      const existingUser = await users.findByNormalizedEmail(
        email.normalizedValue,
      );

      if (existingUser) {
        throw new UserEmailAlreadyUsedError();
      }

      await users.add(user);
      await outbox.addAll(events);

      return { userId: user.id.value };
    });
  }
}
```

密码哈希等 CPU 密集操作必须在开启数据库事务前完成。只有必须原子提交的数据库读写进入事务；Outbox 与业务数据使用同一事务仓储写入。

### 7.3 Module 是组装点

以下代码是依赖组装节选，省略 import；`DatabaseModule` 负责导出已验证的数据库配置 Provider 和 `DatabaseClient`。

```ts
/**
 * Module 只连接抽象与实现，并声明模块公开入口。
 *
 * Prisma Repository 不导出给其他业务模块，
 * 跨模块只能依赖 UsersFacade 或公开契约。
 */
@Module({
  imports: [DatabaseModule],
  controllers: [UsersController],
  providers: [
    CreateUserUseCase,
    UsersFacade,
    {
      provide: PasswordHasher,
      useClass: Argon2PasswordHasher,
    },
    {
      provide: UserIdGenerator,
      useClass: UuidV7UserIdGenerator,
    },
    {
      provide: UsersUnitOfWork,
      useClass: PrismaUsersUnitOfWork,
    },
  ],
  exports: [UsersFacade],
})
export class UsersModule {}
```

基础设施模块不应默认 `@Global()`。显式 `imports` 更容易看出依赖，也能减少隐式耦合。确需全局的 Logger、配置上下文等能力也应保持极少，并记录原因。

### 7.4 Controller 保持轻量

Controller 只能做四件事：

1. 接收验证后的 DTO。
2. 映射为 Command / Query。
3. 调用一个明确 Use Case。
4. 用 Presenter 映射响应 DTO。

Controller 禁止直接注入 Prisma、开启事务、判断核心业务规则、捕获所有异常或使用 `@Res()` 手工控制常规响应。

## 8. Prisma 7 基础设施

### 8.1 Client 生成与 CLI 配置

新项目使用 Prisma 7 的 `prisma-client` 生成器并显式声明输出目录，不再把 `prisma-client-js` 作为新系统基线：

```prisma
/// Prisma Client 生成到应用源码树中的固定基础设施目录。
/// 构建、测试和部署必须先执行 prisma generate。
generator client {
  provider = "prisma-client"
  output   = "../src/generated/prisma"
}

/// 连接地址由 prisma.config.ts 提供。
/// Schema 中只声明数据库类型，不读取环境变量。
datasource db {
  provider = "postgresql"
}
```

生成目录属于机器产物，推荐不提交 Git，在本地安装、CI 构建和镜像构建中显式执行 `prisma generate`。所有环境使用 Lockfile 固定的同一 Prisma CLI 和 Client 版本。

```ts
import 'dotenv/config';
import { defineConfig, env } from 'prisma/config';

/**
 * Prisma CLI 的配置入口只负责 Schema、迁移目录和连接地址。
 *
 * 运行时的完整配置仍由 NestJS 配置层校验，
 * 两者不能在业务代码中重复读取环境变量。
 */
export default defineConfig({
  schema: 'prisma/schema.prisma',
  migrations: {
    path: 'prisma/migrations',
  },
  datasource: {
    url: env('MIGRATION_DATABASE_URL'),
  },
});
```

`env()` 会让 `prisma generate` 等不连接数据库的命令也要求该变量存在。可以在镜像构建前生成 Client，或在 generate 阶段把 `MIGRATION_DATABASE_URL` 显式设为语法合法、非敏感且永不连接的构建占位 URL；绝不能把生产数据库凭证注入构建层。Migration Job 才获得真实环境的直连 Secret。

建议脚本职责：

```text
prisma:generate        生成 Prisma Client
prisma:validate        校验 Schema
prisma:migrate:dev     本地创建迁移
prisma:migrate:deploy  CI、预发布和生产应用迁移
prisma:studio          仅本地数据检查
```

### 8.2 一个进程一个 Prisma Client

- 一个 NestJS 进程只创建一个共享 Prisma Client。
- 禁止每个请求、Repository、定时任务或消费者自行 `new PrismaClient()`。
- Client 由明确的 `DatabaseModule` 管理生命周期；该模块不设为全局。
- 只有基础设施持久化适配器可以注入数据库 Client。
- 应用启动时建立连接，关闭时释放连接并启用 NestJS Shutdown Hooks。

```ts
/**
 * DatabaseClient 是 PostgreSQL 连接和 Prisma 生命周期的唯一入口。
 *
 * 连接池大小来自已验证配置；业务模块既不能读取连接串，
 * 也不能自行创建新的 Prisma Client。
 */
@Injectable()
export class DatabaseClient
  extends PrismaClient
  implements OnModuleInit, OnApplicationShutdown
{
  constructor(config: DatabaseConfig) {
    const adapter = new PrismaPg({
      connectionString: config.url,
      max: config.pool.max,
      connectionTimeoutMillis: config.pool.connectTimeoutMs,
      idleTimeoutMillis: config.pool.idleTimeoutMs,
    });

    super({ adapter });
  }

  async onModuleInit(): Promise<void> {
    await this.$connect();
  }

  async onApplicationShutdown(): Promise<void> {
    await this.$disconnect();
  }
}
```

不要为了“方便”在普通 Service 注入 `DatabaseClient`。如果多个模块都需要数据库，只是分别显式导入 `DatabaseModule`，然后由各自的 Infrastructure Provider 使用。

`enableShutdownHooks()` 只触发生命周期，不会自动完成摘流和排空。应用必须有显式 `ShutdownCoordinator`；数据库放在 `OnApplicationShutdown` 或协调器的最后释放阶段，避免 `OnModuleDestroy` 过早断开仍被在途请求使用的连接。

### 8.3 Repository Adapter 与 Mapper

```ts
import {
  Prisma,
  type User as PrismaUserRecord,
} from '../../../../generated/prisma/client';

/**
 * Prisma 记录到领域实体的转换集中在持久化 Mapper。
 *
 * 生成类型、数据库命名和 Decimal 等实现细节
 * 不得继续传播到 Application 或 Presentation。
 */
export class PrismaUserMapper {
  static toDomain(record: PrismaUserRecord): User {
    return User.restore({
      id: UserId.from(record.id),
      email: Email.restore(record.email, record.emailNormalized),
      status: UserStatus.from(record.status),
      passwordHash: PasswordHash.restore(record.passwordHash),
      version: record.version,
    });
  }

  static toPersistence(user: User): Prisma.UserCreateInput {
    return {
      id: user.id.value,
      email: user.email.value,
      emailNormalized: user.email.normalizedValue,
      passwordHash: user.passwordHash.value,
      status: mapUserStatusToPrisma(user.status),
      version: user.version,
    };
  }
}

/**
 * Repository 只声明自己使用的 Prisma Delegate。
 *
 * 普通 DatabaseClient 与 TransactionClient 都满足该结构，
 * 因而无需把事务类型传播到应用层。
 */
type PrismaUserExecutor = Pick<Prisma.TransactionClient, 'user'>;

/**
 * Adapter 只实现仓储契约和持久化错误翻译。
 *
 * 它不做 HTTP 响应映射，也不把业务判断隐藏在
 * 通用 Prisma Helper 或自动查询改写中。
 */
export class PrismaUserRepository implements UserRepository {
  constructor(private readonly database: PrismaUserExecutor) {}

  async findByNormalizedEmail(email: string): Promise<User | null> {
    const record = await this.database.user.findUnique({
      where: { emailNormalized: email },
    });

    return record ? PrismaUserMapper.toDomain(record) : null;
  }

  async add(user: User): Promise<void> {
    try {
      await this.database.user.create({
        data: PrismaUserMapper.toPersistence(user),
      });
    } catch (error) {
      throw mapPrismaPersistenceError(error);
    }
  }
}
```

通用 Repository 基类只允许抽取完全一致的技术行为，例如错误翻译或分页解码。禁止抽象出接收任意 `where/include/data` 的“万能仓储”，否则 Prisma 细节会重新泄漏到上层。

### 8.4 显式事务上下文

Prisma 的 `TransactionClient` 只能存在于基础设施实现。`PrismaUsersUnitOfWork` 在每次事务中构建绑定到同一 `tx` 的 Repository，再把应用层端口集合交给回调。

```ts
/**
 * Unit of Work 是 Prisma 事务和应用层事务端口的组装边界。
 *
 * 每个 Repository 都由当前 tx 构造，确保同一用例的业务写入
 * 与 Outbox 写入实际使用同一 PostgreSQL 事务。
 */
@Injectable()
export class PrismaUsersUnitOfWork extends UsersUnitOfWork {
  constructor(
    private readonly database: DatabaseClient,
    private readonly transactionConfig: DatabaseTransactionConfig,
  ) {
    super();
  }

  run<T>(work: (transaction: UsersTransaction) => Promise<T>): Promise<T> {
    return this.database.$transaction(
      (tx) => work(this.createTransaction(tx)),
      {
        maxWait: this.transactionConfig.maxWaitMs,
        timeout: this.transactionConfig.timeoutMs,
      },
    );
  }

  runSerializable<T>(
    work: (transaction: UsersTransaction) => Promise<T>,
  ): Promise<T> {
    return runPrismaSerializable(
      this.database,
      this.transactionConfig,
      (tx) => work(this.createTransaction(tx)),
    );
  }

  /**
   * 所有事务入口复用同一个上下文构造器。
   *
   * 新增仓储时只修改这里，避免普通事务与可序列化事务
   * 获得不同的资源集合。
   */
  private createTransaction(
    tx: Prisma.TransactionClient,
  ): UsersTransaction {
    return {
      users: new PrismaUserRepository(tx),
      outbox: new PrismaOutboxRepository(tx),
    };
  }
}
```

`connectionTimeoutMillis` 和 `idleTimeoutMillis` 属于 `pg.Pool`；Prisma interactive transaction 的 `maxWait` / `timeout` 在 Unit of Work 明确配置；PostgreSQL 的 `statement_timeout`、`lock_timeout` 与 `idle_in_transaction_session_timeout` 通过 Runtime Role 默认值或受控连接参数配置，特殊用例才在事务内使用 `SET LOCAL`。三层超时不能混为一个参数。

Application 根据不变量选择语义明确的 `run` 或 `runSerializable`，但永远不传 Prisma 隔离级别。若只有个别用例需要特殊一致性，可以为该用例定义更窄的专用 Unit of Work Port。

禁止以下方式：

- 把 `TransactionClient` 作为参数传进 Domain。
- 用可变全局变量保存当前事务。
- Repository 表面进入事务，内部仍使用普通 Client。
- 多个 Service 各自开启互相不可见的嵌套事务。
- 依靠隐式异步上下文隐藏关键事务边界。

## 9. PostgreSQL 数据建模

### 9.1 标识符策略

项目开始时必须统一主键策略：

- 高写入、数据库内部使用的表可以选 `bigint identity`，索引局部性好。
- 需要跨节点生成、客户端预生成或避免暴露递增序号时使用 UUID。
- 高写入 UUID 场景可以统一采用 UUIDv7，但必须由固定的数据库能力或应用 `IdGenerator` 生成，禁止各模块自行选择算法。
- 使用 `BigInt` 时，API 必须序列化为字符串，不能直接交给 JSON。
- 订单号等业务编号独立建唯一约束，不承担数据库主键职责。
- 同一系统不要无规则混用 UUID、CUID 和数字 ID。
- 聚合若要在持久化前产生领域事件或 Outbox，必须由注入的 `IdGenerator` 先生成 ID，再显式写入数据库；数据库默认值只用于不需要预持久化 ID 的简单记录。

### 9.2 推荐 Schema 示例

```prisma
/// 订单是独立聚合根。
/// 跨请求与跨进程并发下的数据正确性最终由约束保护。
model Order {
  id          String      @id @db.Uuid
  tenantId    String      @map("tenant_id") @db.Uuid
  orderNumber String      @map("order_number") @db.VarChar(64)
  status      OrderStatus
  totalAmount Decimal     @map("total_amount") @db.Decimal(19, 4)
  currency    String      @db.VarChar(3)
  version     Int         @default(0)
  createdAt   DateTime    @default(now()) @map("created_at") @db.Timestamptz(3)
  updatedAt   DateTime    @updatedAt @map("updated_at") @db.Timestamptz(3)
  items       OrderItem[]

  @@unique([tenantId, orderNumber], map: "uq_orders_tenant_number")
  @@index([tenantId, status, createdAt(sort: Desc), id(sort: Desc)], map: "idx_orders_tenant_status_page")
  @@map("orders")
}

/// 订单行的生命周期完全从属于订单。
/// 只有这种聚合内部关系才默认允许级联删除。
model OrderItem {
  orderId  String  @map("order_id") @db.Uuid
  lineNo   Int     @map("line_no")
  sku      String  @db.VarChar(128)
  quantity Int
  unitPrice Decimal @map("unit_price") @db.Decimal(19, 4)

  order Order @relation(fields: [orderId], references: [id], onDelete: Cascade)

  @@id([orderId, lineNo])
  @@index([sku], map: "idx_order_items_sku")
  @@map("order_items")
}

/// 状态值集合稳定且由代码状态机控制，因此使用数据库枚举。
/// 运营可配置的分类不得建成原生枚举，应使用配置表。
enum OrderStatus {
  PENDING   @map("pending")
  CONFIRMED @map("confirmed")
  CANCELLED @map("cancelled")

  @@map("order_status")
}
```

约定：

- Prisma 使用 TypeScript 风格命名，数据库统一 `snake_case`，通过 `@map` / `@@map` 显式映射。
- 重要索引和约束显式命名，方便迁移、错误映射和监控定位。
- 可空字段必须有真实业务语义，不得仅为“以后再处理”而 nullable。
- `@updatedAt` 由 Prisma 写入维护。如果还有 ETL、脚本或其他应用直接写库，应改由数据库触发器统一维护。
- `Timestamptz(3)` 与 JavaScript `Date` 的毫秒精度一致。

### 9.3 类型选择

| 业务语义 | PostgreSQL / Prisma 建议 | 规则 |
| --- | --- | --- |
| 时间点 | `timestamptz(3)` / `DateTime` | 数据库和服务端按 UTC；展示时转换时区 |
| 纯日期 | `date` | 生日、结算日等不应转成 UTC 时间点 |
| 本地营业时间 | 本地时间 + IANA 时区 | 单独保存 `Asia/Shanghai` 等时区规则 |
| 金额 | `decimal(p,s)` / `Decimal` | 应用使用金额值对象；API 输出字符串；禁止 `number` 计算 |
| 固定精度简单金额 | 最小货币单位整数 | 项目内只能选定一种主策略 |
| 货币 | ISO 4217 三字符代码 | 金额必须伴随货币；不同币种不能直接相加 |
| 普通文本 | `text` | 没有业务长度限制时不必随意设 `varchar(255)` |
| 有明确长度文本 | `varchar(n)` | `n` 来自业务规则，不来自习惯 |
| 可变附加结构 | `jsonb` / `Json` | 必须校验结构；常查询字段提升为普通列 |
| 稳定小集合 | PostgreSQL enum | 状态机仍由 Domain 控制 |
| 可运营配置集合 | 关系表 | 支持名称、排序、停用、权限等属性 |

金额不得使用 PostgreSQL `money` 或 JavaScript `number`。Decimal 的舍入模式、发生时机和税计算顺序集中到值对象或领域服务。

### 9.4 关系与删除策略

- PostgreSQL 使用数据库真实外键；不得把 `relationMode = "prisma"` 作为新系统默认值。
- 一对多外键放在“多”的一侧。
- 只要多对多关联可能出现角色、顺序、状态、有效期或审计字段，就建立显式关联模型。
- `Cascade` 只用于生命周期完全从属于父聚合的子实体。
- 用户、订单、账务、审计等跨聚合关系通常使用 `Restrict` 或历史快照。
- `SetNull` 只能用于可空外键，并明确父记录消失后的业务语义。
- PostgreSQL 不自动为外键引用列创建索引；用于 Join、过滤或父记录删除检查的外键应显式建索引。
- 多租户自然唯一约束必须包含 `tenant_id`；高风险关系可用含 `tenant_id` 的组合外键阻止跨租户引用。
- 不用逗号字符串、数组或 JSON 保存本应由外键维护的实体关系。

### 9.5 约束是最终防线

数据库必须保护：

- 主键、自然唯一键和幂等键。
- 外键存在性。
- 必填字段。
- 数量、金额、库存的合法范围。
- 开始时间早于结束时间。
- 可由单行或数据库约束准确表达的关键不变量。

Prisma Schema 无法完整表达的 `CHECK`、表达式索引、部分索引、`EXCLUDE` 等能力，应写入自定义迁移 SQL，并继续纳入 Prisma 迁移历史。

```sql
/*
 * 数据库从最终层面阻止负库存。
 * 应用的预检查只用于提供更友好的业务错误。
 */
ALTER TABLE inventory
ADD CONSTRAINT ck_inventory_available_nonnegative
CHECK (available >= 0);

/*
 * 租户内组织名称的大小写归一化唯一性由数据库保证。
 * 查询时使用不区分大小写模式不能替代唯一约束。
 */
CREATE UNIQUE INDEX CONCURRENTLY uq_organizations_tenant_name_normalized
ON organizations (tenant_id, lower(name));

/*
 * Prisma Schema 不能完整表达这些范围与格式规则，
 * 因此用命名 CHECK 作为订单数据的最终保护。
 */
ALTER TABLE orders
ADD CONSTRAINT ck_orders_total_amount_nonnegative
CHECK (total_amount >= 0),
ADD CONSTRAINT ck_orders_currency_format
CHECK (currency ~ '^[A-Z]{3}$');

ALTER TABLE order_items
ADD CONSTRAINT ck_order_items_line_positive
CHECK (line_no > 0),
ADD CONSTRAINT ck_order_items_quantity_positive
CHECK (quantity > 0),
ADD CONSTRAINT ck_order_items_unit_price_nonnegative
CHECK (unit_price >= 0);
```

货币 CHECK 只保证三位大写格式；有效 ISO 4217 集合由 `Currency` 值对象或受控货币表维护，不能把正则当作完整币种校验。

`CREATE INDEX CONCURRENTLY` 不能在事务块中执行。迁移前必须确认 Prisma 生成 SQL 的事务和锁行为，不得把这类 SQL 直接塞进未经审查的自动迁移。

### 9.6 索引设计

- 主键和唯一约束已有索引，不重复创建。
- 组合索引通常把等值过滤列放前面，把范围与排序列放后面。
- 索引必须匹配真实 `WHERE`、`JOIN`、`ORDER BY`，遵循最左前缀。
- JSONB 包含和路径查询评估 GIN；时间追加型超大表评估 BRIN。
- 高频写表避免无依据的多索引，索引会增加写放大、WAL 和 Vacuum 成本。
- 使用 `pg_stat_statements` 发现热点，再用接近生产的数据量执行 `EXPLAIN (ANALYZE, BUFFERS)` 验证。
- 在生产执行带 `ANALYZE` 的语句前评估实际执行成本。
- `EXPLAIN ANALYZE` 会真实执行语句；对 `INSERT`、`UPDATE`、`DELETE` 只在安全环境或受控可回滚事务中使用，并评估锁和外部副作用。
- 定期清理重复、失效和长期无价值索引，但必须先验证业务与周期性任务流量。

### 9.7 JSONB 使用边界

JSONB 只适合结构可变、主要整体读取、没有稳定关系语义的附加数据。以下字段应提升为普通列或关系表：

- 频繁参与过滤、排序、Join、唯一性或统计的字段。
- 需要外键保护的 ID。
- 有稳定领域含义和独立生命周期的数据。

重要 JSON 必须在入口校验结构，必要时保存 `schemaVersion`。代码必须区分 SQL `NULL`、JSON `null` 和字段不存在；不得把 `DbNull`、`JsonNull`、`AnyNull` 混用。

### 9.8 删除、审计与归档

新系统默认选择：

1. 真删除非核心临时数据。
2. 保留独立审计日志或领域事件。
3. 有法规或历史查询需求时归档到明确的历史表或冷存储。

只有业务明确需要恢复，或“删除”本身就是可逆状态时才使用软删除。采用软删除必须同时设计：

- `deleted_at`、删除人和原因。
- `WHERE deleted_at IS NULL` 的部分唯一索引。
- 恢复时的唯一冲突处理。
- 父子记录语义和最终物理清理策略。
- 普通、已删除和审计查询的明确 Repository 方法。

禁止通过 Prisma Client Extension 或其他全局机制隐式给所有查询追加 `deletedAt: null`。这会漏掉原生 SQL、聚合和嵌套关系，也会让查询语义不可推导。

## 10. 查询与性能

### 10.1 查询形状

- 使用 `select` 只读取用例需要的字段。
- `include` 必须有明确边界；一对多子集合要有限制、排序和过滤。
- 所有 `findMany` 必须有数量上限。
- 禁止在循环中逐条查询；先收集 ID 后批量查询并在内存分组。
- GraphQL Resolver 等批量解析场景按需使用 request-scoped DataLoader；缓存不能跨请求共享。
- 汇总多个父实体的数据，优先一次 `groupBy` 或带父 ID 的聚合查询。
- 用 `$transaction` 包住 N 次查询并不会消除 N+1。
- 大批量写入使用 `createMany` 并合理分批；超大导入使用受控 `COPY` 或暂存表流程。
- 原生 SQL 集中在基础设施 Adapter：返回记录使用 tagged-template `$queryRaw`，更新、删除和 DDL 等无结果集语句使用 tagged-template `$executeRaw`，复杂片段使用安全的 `Prisma.sql` 组合。
- 禁止 `$queryRawUnsafe`、`$executeRawUnsafe` 和任何不可信字符串拼接。

动态排序、字段名和筛选操作符不能直接来自客户端，必须经过固定白名单映射。

### 10.2 分页选择

Offset 分页适合数据量可控、需要跳页的后台列表。必须限制最大 `skip` 和页大小，并避免默认对大表执行精确 `COUNT(*)`。

大数据集和连续滚动默认使用 Keyset/Cursor 分页：

- 排序稳定，末尾添加唯一字段作为平局裁决。
- Cursor 包含全部排序值和方向，并编码或签名。
- 查询 `pageSize + 1` 条判断下一页。
- 组合索引与过滤和排序顺序一致。
- 不用频繁变化字段作为游标。

以下实现位于 Infrastructure Read Repository，`Prisma.OrderWhereInput` 不会作为 Application 或 HTTP 契约返回：

```ts
/**
 * createdAt 可能相同，因此 id 负责确定严格且唯一的顺序。
 *
 * 下一页条件必须和降序排序一致，避免并发插入时
 * 出现明显的重复或漏读。
 */
const cursorFilter: Prisma.OrderWhereInput = cursor
  ? {
      OR: [
        { createdAt: { lt: cursor.createdAt } },
        {
          createdAt: cursor.createdAt,
          id: { lt: cursor.id },
        },
      ],
    }
  : {};

const rows = await database.order.findMany({
  where: {
    tenantId,
    status,
    ...cursorFilter,
  },
  orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
  take: pageSize + 1,
  select: {
    id: true,
    orderNumber: true,
    status: true,
    createdAt: true,
  },
});
```

对应索引应覆盖 `(tenant_id, status, created_at DESC, id DESC)`，与 Schema 示例一致。如果 `status` 是可选条件，应把“带状态”和“不带状态”视为两种查询形状，依据执行计划决定是否增加另一条索引，不能假设一条组合索引覆盖所有场景。

首个 Cursor 保存 `snapshotAt` 并在后续限制 `created_at <= snapshotAt`，只能排除之后创建的新记录，形成近似稳定窗口；它不能阻止已有记录被删除、更新或回填时间。真正固定的数据集需要物化结果、持久快照或符合业务语义的版本边界。

### 10.3 搜索与统计

- `contains` 模糊查询不适合无限增长的大表。
- 前缀、全文、相似度搜索分别评估 B-tree + 合适运算符类 / 排序规则（如 `text_pattern_ops`）、PostgreSQL FTS、`pg_trgm` 或独立搜索引擎。
- 大表精确总数是显式功能，不是所有分页响应的默认字段。
- 报表和复杂聚合不要持续挤占在线事务库；按规模选择物化视图、只读副本或独立分析链路。
- 缓存只能解决已确认的重复读取瓶颈，不能用来掩盖错误索引和低效查询。

### 10.4 连接池预算

连接上限按整个系统计算：

```text
最大应用副本数 × 每副本进程数 × 每进程池上限
+ 后台 Job 与消费者连接
+ 迁移、监控和运维保留连接
< PostgreSQL max_connections - 安全预留
```

必须监控活跃连接、空闲连接、Pool 等待、获取超时、长事务和 `idle in transaction`。不要通过无限提高 `max_connections` 解决慢查询。

应同时配置：

- 连接获取超时。
- 查询 `statement_timeout`。
- DDL / 锁等待 `lock_timeout`。
- `idle_in_transaction_session_timeout`。
- 应用事务 `maxWait` 与 `timeout`。

使用 PgBouncer transaction pooling 时，要针对当前 Prisma 7、`@prisma/adapter-pg`、Prepared Statement 和会话语义进行集成验证。迁移、DDL 和需要会话语义的操作使用直连地址。

一旦启用池化地址，运行时 `DATABASE_URL` 与迁移用直连地址必须是两个独立、必填配置；`prisma.config.ts` 明确读取直连地址，禁止用静默 fallback 猜测连接目标。

## 11. 事务、并发与可靠副作用

### 11.1 事务选择

- 单个聚合的一次创建或更新，优先使用 Prisma nested write。
- 多个互不依赖、但业务上必须原子提交的数据库操作可以使用数组形式 `$transaction([...])`。
- “读取结果决定后续写入”使用 interactive transaction。
- 事务只包含必须原子完成的数据库操作，并设置短超时。
- 禁止在事务中调用 HTTP、RPC、邮件、对象存储或消息队列。
- 禁止在事务中执行密码哈希、大文件处理和长时间 CPU 计算。
- Interactive transaction 使用同一连接，`Promise.all` 不会让查询真正并行。

事务边界属于 Use Case。Controller、领域实体和通用 Helper 不开启事务。

### 11.2 隔离级别

| 隔离级别 | 适用场景 | 注意事项 |
| --- | --- | --- |
| `ReadCommitted` | 普通 CRUD、原子更新、唯一约束保护的流程 | PostgreSQL 默认值；同一事务的两次查询可能看到不同已提交结果 |
| `RepeatableRead` | 事务内需要稳定快照 | 仍需处理并发写入冲突 |
| `Serializable` | 配额、排班、范围约束、跨多行不变量 | 序列化失败是正常结果，必须有界重试 |

不要把所有事务全局设为 `Serializable`。应针对不变量选择最低且正确的隔离级别，并结合原子 SQL、约束、乐观锁或行锁。

### 11.3 Serializable 有界重试

下例是 `PrismaUsersUnitOfWork` 等 Infrastructure 实现内部使用的机制，不是 Application 可见 API。Use Case 仍只调用 `UsersUnitOfWork`，Prisma 类型不会越过基础设施边界。

```ts
/**
 * 每次尝试都重新开启完整事务，不能复用失败事务。
 *
 * 回调只允许包含可重放的数据库逻辑，外部副作用
 * 必须通过 Outbox 在提交后处理。
 */
async function runPrismaSerializable<T>(
  database: DatabaseClient,
  config: DatabaseTransactionConfig,
  work: (tx: Prisma.TransactionClient) => Promise<T>,
): Promise<T> {
  for (
    let attempt = 1;
    attempt <= config.serializableMaxAttempts;
    attempt += 1
  ) {
    try {
      return await database.$transaction((tx) => work(tx), {
        isolationLevel: Prisma.TransactionIsolationLevel.Serializable,
        maxWait: config.maxWaitMs,
        timeout: config.timeoutMs,
      });
    } catch (error) {
      if (
        !isRetryableTransactionConflict(error) ||
        attempt === config.serializableMaxAttempts
      ) {
        throw error;
      }

      /**
       * 指数退避必须带随机抖动，避免并发请求
       * 在同一时刻再次产生冲突。
       */
      await delayWithJitter(attempt, config.retryBaseDelayMs);
    }
  }

  throw new Error('不可达分支');
}
```

`isRetryableTransactionConflict()` 是基础设施错误归一化的唯一入口，需要解包当前 Driver Adapter 的错误链，并识别 Prisma `P2034` 与 PostgreSQL SQLSTATE `40001` / `40P01`；必须用当前真实驱动写集成测试。`P2002` 通常是确定的业务唯一冲突，不应无条件重试。

### 11.4 原子更新

不要“先读库存、在内存计算、再写回”。让条件判断与更新在一条数据库语句内完成：

```ts
/**
 * 库存检查和扣减由 PostgreSQL 原子执行。
 *
 * quantity 是进入仓储前已验证为大于零的 PositiveQuantity，
 * 禁止把客户端原始 number 直接传入原子更新。
 *
 * count 为零表示库存不足或记录不存在，
 * 应由 Application 转换为明确领域错误。
 */
const result = await tx.inventory.updateMany({
  where: {
    tenantId,
    id: inventoryId,
    available: { gte: quantity.value },
  },
  data: {
    available: { decrement: quantity.value },
  },
});

if (result.count !== 1) {
  throw new InventoryUnavailableError();
}
```

数据库仍应有 `CHECK (available >= 0)`，以防其他写入路径破坏不变量。示例有意用“不存在或库存不足”的统一错误避免资源枚举；如果产品必须区分，只能在已授权上下文中追加明确查询。

### 11.5 乐观锁与悲观锁

冲突不频繁、需要阻止覆盖更新时使用显式 `version`：

```ts
/**
 * version 是明确的并发令牌。
 *
 * 只有调用者读取到的版本仍有效时才更新，
 * 失败必须返回 409 或进入明确的业务重试。
 */
const result = await tx.order.updateMany({
  where: {
    tenantId,
    id: orderId,
    version: expectedVersion,
  },
  data: {
    status: nextStatus,
    version: { increment: 1 },
  },
});

if (result.count !== 1) {
  throw new ConcurrentModificationError();
}
```

`count = 0` 同时可能表示记录不存在或版本冲突。Application 必须明确采用统一不可用错误，或在已授权上下文中重新查询后区分，不能让基础设施层猜测 HTTP 语义。

确需“锁定后再计算”时，在基础设施事务内使用参数化 `SELECT ... FOR UPDATE`。多行加锁按稳定顺序执行以降低死锁；任务抢占可评估 `SKIP LOCKED`；禁止把 Advisory Lock 作为普通业务锁的默认实现。

### 11.6 幂等

所有可能重放的操作都应设计幂等性，例如创建订单、支付回调、Webhook、消息消费和批量任务。

- 幂等键必须有数据库唯一约束。
- 多租户唯一范围至少包含 `(tenant_id, operation, idempotency_key)` 或等价命名空间，避免同一键在不同业务动作间误冲突。
- 幂等记录与业务写入在同一事务提交。
- 同一 key + 同一请求摘要返回原结果。
- 同一 key + 不同请求摘要返回冲突，不能静默复用。
- 请求摘要必须由版本化的规范化算法生成，明确字段排序、缺省值、编码和算法升级策略。
- 记录明确的处理状态、响应摘要和过期策略。
- “先查 key 再插入”仍有竞争，必须处理唯一冲突。

### 11.7 Transactional Outbox

数据库提交与消息发送不能靠“先保存，再直接 publish”保证一致。推荐流程：

```text
业务事务：更新聚合 + 插入 outbox_events
                   ↓ 同一事务提交
Publisher 短事务：带锁领取 + 写入租约 / 处理中状态 → 提交
                   ↓ 事务外发布消息
Publisher 短事务：按 event_id 标记完成
                   ↓
Consumer：按 event_id 幂等处理
```

规则：

- Outbox 记录包含稳定事件 ID、类型、版本、聚合 ID、发生时间和 payload。
- Publisher 支持重试、退避、死信和停滞监控。
- 发布是至少一次语义，消费者必须幂等。
- 发布期间不持有数据库事务或行锁；Publisher 崩溃后由过期租约重新领取，因此允许重复发布。
- 事件是模块公开契约，版本升级要有明确策略。
- 领域事件不得依赖 NestJS EventEmitter 隐藏核心强一致调用链。
- 事件 payload 只带完成消费所需的最小数据，不复制整个数据库对象。

## 12. Prisma Migrate 与数据库发布

### 12.1 命令边界

| 命令 | 允许环境 | 用途 |
| --- | --- | --- |
| `prisma migrate dev --name <name>` | 本地开发 | 根据 Schema 创建并应用迁移 |
| `prisma migrate dev --create-only` | 本地开发 | 先生成 SQL，再人工编辑和审查 |
| `prisma migrate deploy` | CI、测试、预发布、生产 | 只执行仓库中已有迁移 |
| `prisma db push` | 可随时销毁的个人原型 | 不生成可审计迁移历史 |
| `prisma migrate reset` | 明确可销毁的本地/测试库 | 重建数据库；生产绝对禁止 |

共享环境和生产不得使用 `db push`，也不得手工只改数据库而不产生迁移文件。

### 12.2 迁移文件规则

- Schema 与迁移 SQL 一起提交评审。
- 已进入共享分支或部署过的迁移视为不可变制品；修复必须新增迁移。
- 一个迁移只表达一个可解释的结构变化。
- 自动生成 SQL 必须人工审查，尤其防止重命名被识别为“删除 + 新增”。
- 扩展、触发器、检查约束、部分索引、表达式索引全部进入迁移历史。
- Seed 与结构迁移分离；Seed 必须幂等，并且不能写生产测试账号或固定弱密码。
- 大批量数据回填使用可重入、可分批、可限速、有检查点的专用任务。

### 12.3 迁移 SQL 审查

每个迁移至少检查：

- 是否导致表重写或长时间锁表。
- 新增列的默认值和 `NOT NULL` 是否会扫描大表。
- 唯一索引前是否已验证重复数据。
- 外键前是否已验证孤儿数据。
- 索引字段顺序是否匹配真实查询。
- 类型转换是否丢失精度或无法转换。
- 是否需要 `lock_timeout`、`statement_timeout`。
- 是否包含不能位于事务块的 DDL。
- 失败后是前滚修复、人工处置还是从 PITR 恢复。

每个迁移必须显式决定事务策略：需要全有或全无时在 SQL 中建立明确事务边界；`CREATE INDEX CONCURRENTLY` 等不能位于事务块的操作拆成独立迁移。非事务多语句迁移可能部分成功，必须预先定义幂等前滚和人工处置方案。

Prisma Migrate 不能保证所有 PostgreSQL DDL 和数据变更都能自动安全回滚。生产默认采用前滚修复。`prisma migrate resolve` 只能在人工核实数据库真实状态、保存 SQL 证据和处置记录后使用，不能用来简单跳过失败迁移。

`prisma migrate deploy` 负责根据迁移历史应用待执行迁移，但不等价于完整 Schema drift 检测。发布流程应使用只读 `migrate diff`、系统目录断言或等价机制核对关键表、列、约束和索引；`migrate status` 不能替代实际结构检查。

### 12.4 大表与破坏性变更

对大表增加非空字段的一般流程：

1. 增加允许为空的新字段。
2. 新版本开始写入该字段。
3. 后台任务分批回填并记录进度。
4. 验证数据后添加约束。
5. 将字段设为 `NOT NULL`。
6. 删除只服务于发布过程的临时结构和代码。

PostgreSQL 在线变更可以评估 `CHECK (...) NOT VALID` → `VALIDATE CONSTRAINT` → `SET NOT NULL`，以及外键 `NOT VALID` 后再验证；索引评估 `CREATE INDEX CONCURRENTLY`。这些手段仍会产生短时锁、额外扫描、WAL 和磁盘开销，必须用生产形状数据验证。

本系统不保留长期双读、双写、废弃字段或兼容分支。若业务不要求零停机，破坏性变更优先使用维护窗口停止写入并一次性切换；若必须滚动发布，只允许有明确开始和结束条件的短期分阶段发布，完成后立即清理。

### 12.5 生产发布顺序

迁移由一次性 Job 执行，不能让每个应用副本启动时争抢。发布前必须在以下两种流程中明确选择一种。

**维护窗口流程**：

```text
确认备份 / PITR 和恢复方案
  → 停止入口写流量并排空旧应用 / Worker
  → 执行完整 Migration Job
  → 校验迁移状态和关键约束
  → 部署新应用并执行启动检查和核心冒烟
  → 恢复流量
  → 观察错误率、延迟、连接、锁和慢查询
```

**滚动发布流程**：

```text
Release A：Expand 迁移，只增加旧、新应用都能安全使用的结构
  → 滚动部署新应用
  → 确认旧 Pod、旧 Worker 和旧在途任务全部退出
  → 分批回填并验证生产形状数据
  → Release B：独立 Contract 迁移，删除旧结构
  → 删除仅服务于本次切换的过渡代码
```

Expand 与 Contract 不得合并成同一次滚动发布迁移。短期过渡结构是受控发布状态，不是长期 legacy、双路径或 fallback；必须有负责人、截止发布和自动检查。

Migration Job 必须单飞运行，配置超时、失败停止发布、有限重试和日志保留。推荐从同一提交构建独立不可变 Migration Image，其中包含固定版本 Prisma CLI、`prisma.config.ts`、Schema 和 migrations；发布清单把 Application Image Digest、Migration Image Digest、预期最后迁移名及校验和一一绑定。

应用回滚只有在当前数据库结构仍兼容旧应用时才安全。删除列、修改类型和批量改写前必须明确前滚修复与恢复策略。

## 13. 安全设计

### 13.1 默认拒绝

- 全局注册认证 Guard，接口默认受保护；公开接口只能用明确 `@Public()` 标记。
- 认证回答“是谁”，授权回答“能否对该资源执行该动作”，两者分离。
- 未认证返回 `401`，无权限返回 `403`。
- RBAC 负责粗粒度能力；资源所有权、组织、租户和状态规则由 Policy / ABAC 表达。
- 前端权限只改善体验，最终授权必须在服务端每个用例执行。
- 对列表查询和单资源操作都执行对象级权限过滤，不能只隐藏按钮。

禁止把授权散落为 `if (user.role === 'admin')`。应建立可单测的策略，例如 `UserPolicy.can(actor, 'update', target)`。

### 13.2 身份与令牌

- 密码使用 Argon2id 等专用密码哈希算法，参数集中配置并可升级。
- JWT 严格验证签发者、受众、有效期、算法和 key ID。
- JWT Header 中的 `kid` 来自不可信输入，只能在固定 issuer 的 JWKS 或受控密钥集合中查找；限制算法，未知 `kid` 必须失败关闭。
- Access Token 短期有效，只携带必要身份声明，不保存敏感数据和庞大权限快照。
- Refresh Token 支持轮换、撤销和重放检测，并以哈希形式持久化。
- 登录、验证码、找回密码和高成本搜索接口限流。
- Cookie 认证必须防 CSRF；CORS 使用明确来源白名单。
- 全站使用 HTTPS；Cookie 按场景配置 `Secure`、`HttpOnly`、`SameSite`，并启用 HSTS。CORS 不是 CSRF 防护替代品。
- 特权账号启用 MFA，并对导出、授权、密钥和高风险批量操作执行 step-up authentication。
- 登录与找回流程防凭证填充和用户枚举，对外保持一致失败语义，内部保留可审计原因。
- 会话、令牌与密码重置等安全状态必须可审计。

### 13.3 输入、输出与原生 SQL

- 全局严格 DTO 校验，拒绝未声明字段。
- 设置字符串、数组、分页、请求体、文件和查询复杂度上限。
- 文件校验真实类型、尺寸、扩展名、存储路径和访问权限。
- 外部 URL、重定向和回调地址限制协议与目标；DNS 解析后阻断 loopback、link-local、私网和云元数据地址，每次重定向重新验证，并禁止向新目标透传凭证。
- SSRF 防护同时依赖出口 NetworkPolicy / 防火墙，不能只依赖字符串白名单。
- Prisma 常规查询使用参数化 API；原生查询只用 `$queryRaw` 模板参数。
- 动态列名、表名和排序字段使用固定映射，禁止字符串拼接。
- 错误不暴露 SQL、Prisma 错误、堆栈、路径或内部表结构。

### 13.4 数据库权限与网络

至少分离：

- Runtime Role：必要表的最小 DML 权限，无 DDL、扩展和超级用户权限。
- Migration Role：发布 Job 使用的受控 DDL 权限。
- Read-only / Analytics Role：只读副本或限定 Schema 权限。
- Operator Role：受审计的紧急运维权限。

数据库置于私有网络并启用 TLS，生产必须校验证书链和主机名，禁止关闭证书校验。Secret 由 Secret Manager 注入，定期轮换，不写入 `.env` 模板、普通 ConfigMap、镜像或日志。静态加密密钥由 KMS 管理，并限制解密权限。

### 13.5 多租户

- 可信 `tenantId` 来自已认证上下文，不能直接相信客户端提交值。
- Repository 接口显式接收 Tenant Context，查询和写入必须包含租户条件。
- 自然唯一约束通常包含 `tenant_id`。
- 跨租户关联通过组合外键或显式校验阻断。
- 缓存 key、幂等 key、对象存储路径、作业和审计记录都包含租户边界。
- PostgreSQL RLS 可作为纵深防御，但不能替代应用层授权。

若使用 RLS，租户上下文必须在事务内通过 `SET LOCAL` 等受控方式设置，并写真实连接池集成测试。禁止依赖跨请求 Session 状态，以免连接复用造成租户数据泄漏。

因为 `SET LOCAL` 只在事务内有效，启用 RLS 后的租户读请求也必须通过只读 Query Unit of Work 开启短事务、设置租户上下文后再查询；不得让读 Repository 绕开这一入口。

RLS 的 Runtime Role 不能是表所有者、超级用户或拥有 `BYPASSRLS`；必要时对表启用 `FORCE ROW LEVEL SECURITY`。集成测试必须使用真实 Runtime Role，并覆盖缺失租户上下文、事务回滚、连接复用和后台任务；使用 `postgres` 超级用户测试不能证明隔离有效。

### 13.6 审计

审计日志和普通运行日志分离。高风险操作至少记录：

- 谁在何时、从哪个请求执行了什么动作。
- 目标资源与所属租户。
- 变更前后摘要或结构化差异。
- 成功、失败和稳定错误码。
- 关联 `traceId` / `requestId`。

审计采用可验证控制：写入与业务读取权限分离、仅追加、定期导出到独立审计存储；合规场景使用不可变 / WORM 保留或签名链。必须定义保留周期，以及业务事务回滚时安全审计如何独立留存。禁止记录密码、Token、密钥或不必要的完整个人信息。

## 14. 外部系统、缓存与后台任务

### 14.1 外部系统适配器

- 第三方 SDK 只能出现在 Infrastructure Adapter，不得进入 Domain。
- Application 依赖本系统定义的 Port，而不是供应商 SDK 类型。
- 所有外部调用设置连接、请求和整体超时。
- 只对明确可重试的网络错误和幂等操作做有界重试，并使用退避与抖动。
- 非幂等请求只有在供应商支持幂等键时才能自动重试。
- 熔断和降级必须有明确业务语义；不能把失败静默转换为旧数据或空结果。
- 保存供应商请求 ID 并关联 Trace，敏感参数必须脱敏。
- Webhook 先验签、校验时间窗、防重放，再进入幂等业务处理。

### 14.2 缓存

没有测量到收益时不要引入缓存。采用缓存必须写清：

- 数据来源、key 组成和租户边界。
- TTL、最大陈旧时间和失效触发点。
- Cache Aside、Write Through 等一致性模型。
- 穿透、击穿和雪崩防护。
- 缓存不可用时是失败还是回源。
- 指标、容量与删除策略。

权限、额度、库存、支付状态等强一致数据，不能只依据缓存作最终决定。禁止在缓存异常时返回未声明的历史值作为隐式 fallback。

### 14.3 定时任务和消费者

- HTTP 服务与重型 Worker 可以复用领域和应用模块，但使用独立进程入口和资源配置。
- 多副本定时任务必须有数据库租约、队列调度或专用 Scheduler，不能依赖进程内 `setInterval` 假设只运行一次。
- Job 参数、状态、游标、尝试次数和错误持久化。
- 任务可重入、可取消、可限速，并设置最大执行时间。
- 使用 `FOR UPDATE SKIP LOCKED` 等领取机制时写并发集成测试。
- Consumer 默认按至少一次投递设计，基于稳定消息 ID 幂等。
- 失败进入有界重试和死信队列，禁止无限快速重试。

## 15. 测试策略

### 15.1 测试金字塔

| 层级 | 重点 | 数据库 | 外部依赖 |
| --- | --- | --- | --- |
| Domain 单元测试 | 值对象、状态机、不变量、Policy | 无 | 无 |
| Application 单元测试 | 用例编排、错误分支、Port 调用 | Fake Repository | Fake / Stub |
| Repository 集成测试 | Prisma 映射、查询、约束、事务 | 真实 PostgreSQL | 按需 |
| HTTP E2E | Guard、Pipe、Filter、序列化、完整流程 | 真实 PostgreSQL | 不可控依赖用 Stub Server |
| 契约测试 | OpenAPI、事件和第三方协议 | 按需 | 验证协议 |
| 架构测试 | 依赖方向、模块公开边界 | 无 | 无 |

测试重点是风险，不追求固定比例：大量快速单元测试、适量真实数据库集成测试、少量关键 E2E。

### 15.2 单元测试

- Domain 测试不启动 NestJS，直接构造对象。
- Use Case 直接构造或通过轻量 TestingModule 测试，替换抽象 Port。
- 时间、随机数、ID、哈希器通过依赖注入控制。
- 每个测试独立准备状态，禁止依赖执行顺序。
- 覆盖成功、拒绝、边界、并发冲突处理分支和未知依赖错误；真实竞争、锁、死锁和隔离级别必须由独立连接的 PostgreSQL 集成测试验证。
- 不为提高覆盖率而重复测试 NestJS 或 Prisma 框架内部行为。
- 权限、资金、库存和状态机等高风险模块设置更高覆盖要求。

禁止把 Prisma 链式调用 Mock 当作数据库测试。它无法验证 PostgreSQL 的类型、约束、锁、隔离级别、查询计划和真实错误码。

### 15.3 真实 PostgreSQL 集成测试

使用 Testcontainers 或 CI Service Container，PostgreSQL 主版本、扩展、排序规则和时区必须与生产一致。不得使用 SQLite 替代。

测试数据库要创建生产等价的 Runtime Role 与 Migration Role。迁移使用 Migration Role，应用与 RLS 测试使用 Runtime Role；使用超级用户会掩盖缺失 GRANT、Sequence 权限和 RLS 绕过问题。

集成测试必须验证：

- 从空数据库执行完整 `prisma migrate deploy`。
- 从“当前生产版本 Schema + 生产形状合成数据或脱敏快照”执行增量升级。
- 对大表迁移测量锁等待、执行时间、WAL、磁盘余量和失败处置。
- Prisma Mapper 的往返转换。
- 唯一、外键、检查和级联约束。
- 事务提交、回滚、超时、序列化重试和死锁映射。
- 乐观锁、原子更新、幂等与 Outbox。
- Cursor 分页边界和稳定排序。
- 多租户隔离，以及启用 RLS 时的连接复用。
- 关键查询次数，防止 N+1 回归。

按测试 Worker 分配独立数据库，避免并行共享状态。Factory / Test Data Builder 只创建场景需要的最小数据，不依赖庞大全局 Seed。并发测试必须使用独立连接真实并发，不能用串行调用模拟。

### 15.4 E2E 与契约测试

E2E 启动真实 NestJS Application 并从 HTTP 边界调用，至少覆盖：

- 登录、刷新、撤销、`401` 和 `403`。
- DTO 转换、未知字段拒绝和分页上限。
- 全局 Guard、Pipe、Interceptor、Filter。
- 核心业务主路径及高风险错误路径。
- 幂等键、重复提交和并发请求。
- 错误结构、稳定错误码、`requestId` / `traceId`。
- Readiness 语义。

OpenAPI 是对外契约，应从代码生成并在 CI 检查破坏性差异。集成事件和 Webhook 也要有显式版本、Schema 与契约测试。

SIGTERM、端点撤流、HTTP / Consumer 排空和连接关闭属于进程级或容器级测试，不应只靠普通 HTTP E2E 证明。

### 15.5 测试数据

- Migration 创建结构，Seed 只创建本地开发必要的基线数据。
- 测试使用 Builder / Factory 创建明确场景，不从生产复制原始个人数据。
- Snapshot 只用于稳定、可审查的大结构，不用于掩盖未知字段变化。
- 固定时间时使用注入的 `Clock`，不要全局修改系统时间后忘记恢复。
- 需要生产规模行为的性能环境使用合成或已脱敏数据。

性能 / 容量、迁移锁影响、安全扫描、故障注入和恢复演练可以按夜间、定期或发布前运行，不必阻塞每次快速合并请求，但必须有固定频率、负责人和失败门禁。

## 16. 代码质量与 CI

### 16.1 TypeScript 与静态规则

必须开启完整严格模式，至少包括：

- `strict: true`
- `noImplicitAny: true`
- `strictNullChecks: true`
- `noUncheckedIndexedAccess: true`
- `exactOptionalPropertyTypes: true`
- `noFallthroughCasesInSwitch: true`
- `forceConsistentCasingInFileNames: true`

新系统禁止新增未经解释的 `any`、非空断言、`@ts-ignore` 和无边界类型断言。确有边界适配需求时，只允许最小作用域的可审查例外并说明原因。外部输入先按 `unknown` 解析，再转换成已验证类型。

使用 ESLint boundaries、dependency-cruiser、Nx 规则或等价工具强制：

- Domain 不依赖 NestJS、Prisma、HTTP。
- Application 不依赖具体 Infrastructure。
- Controller 不访问 Prisma。
- 模块之间不深度导入和循环依赖。
- 业务模块不访问其他模块拥有的持久化实现。

格式化是显式开发动作。CI 只检查，不自动修改工作树；不要把 `--fix` 作为 CI 唯一 lint 命令。

### 16.2 CI 质量门禁

每个合并请求至少执行：

1. 使用 Lockfile 严格安装依赖。
2. ESLint、格式和架构边界检查，不自动改代码。
3. TypeScript 严格类型检查。
4. `prisma validate` 与 `prisma generate`。
5. Domain / Application 单元测试。
6. 使用真实 PostgreSQL 的 Repository 集成测试。
7. 关键 HTTP E2E 与契约测试。
8. 同时验证空库完整迁移链与当前生产版本的增量升级。
9. NestJS 生产构建。
10. 构建生产镜像，以最终非 Root 用户启动并执行 Readiness 与核心冒烟。
11. 执行漏洞、Secret、许可证、SBOM、制品签名与来源证明检查。

安全扫描必须定义阻断等级、例外审批人和到期时间。生产部署使用按环境隔离的短期身份，并按已验证 Digest 部署，不允许普通 PR Job 持有生产长期凭证。

推荐流水线：

```text
静态检查 + 单元测试
       ↓
真实 PostgreSQL 集成测试
       ↓
迁移验证 + 源码级 E2E + 契约检查
       ↓
构建并扫描不可变生产镜像
       ↓
以最终运行身份启动镜像并执行冒烟
       ↓
同一镜像依次进入预发布和生产
```

禁止不同环境重新构建不同制品。CI 测试库必须是独立临时库，绝不能连接共享开发库或生产库。

### 16.3 依赖升级

- Node.js、NestJS、Prisma 和 PostgreSQL 只使用仍受支持版本。
- Prisma CLI 与 Client 保持相同版本，并通过 Lockfile 固定。
- 升级先阅读官方 Migration Guide，再在真实 PostgreSQL 运行完整迁移、集成和性能测试。
- 定期小步升级，禁止长期积累后一次跨多个大版本。
- 大版本升级不保留旧实现开关；验证完成后直接删除旧路径。

## 17. 日志、指标、追踪与健康检查

### 17.1 结构化日志

生产输出 JSON，建议字段：

- 时间、级别、服务、环境、应用版本。
- `requestId`、`traceId`、`spanId`。
- 规范化路由、HTTP 方法、状态码、耗时。
- 按用途受控的 HMAC / 伪名化 `actorId`、`tenantId`；它们仍可能是可关联数据，必须限制访问和保留期。
- `module`、`useCase`、稳定错误码。
- 数据库操作类型、耗时、返回行数和 Pool 等待，但不记录参数值。

只在系统边界、用例边界和异常边界记录。同一异常由一个明确层级记录完整堆栈，避免多层重复日志。

禁止记录密码、Token、Cookie、Authorization Header、完整请求体、身份证件、银行卡和 Prisma 查询参数。脱敏必须集中实现。

### 17.2 指标

HTTP 使用 RED：

- Rate：请求量。
- Errors：错误率。
- Duration：P50、P95、P99 延迟。

PostgreSQL 至少监控：

- 连接总数、活跃连接、Pool 等待和获取超时。
- 慢查询、锁等待、死锁、长事务、回滚率。
- Buffer Cache 命中率、表与索引膨胀、Autovacuum。
- 磁盘、WAL 增长和只读副本延迟。

Pool 等待从真实 `pg.Pool` 或适配层暴露；慢查询与总耗时来自 `pg_stat_statements`、数据库日志或托管服务指标。SQL 指标使用规范化指纹，不能把原始 SQL 和参数作为标签。

业务指标围绕真实成功结果，例如登录成功率、订单创建成功率、Outbox 停滞时长。指标标签必须低基数，禁止使用用户 ID、请求 ID、原始 URL 或异常消息作为标签。

### 17.3 分布式追踪

使用 OpenTelemetry 串联 HTTP、应用用例、PostgreSQL、消息与第三方调用。业务 Span 围绕 Use Case 建立，不要为每个小函数创建 Span。Trace 不记录敏感数据或完整 SQL 参数。

Head sampling 在请求开始时还不知道最终错误和延迟；若要优先保留错误与慢请求，应在 Collector 使用 tail-based sampling。只使用 head sampling 时，要配合错误事件、独立日志和合理基础采样率。

### 17.4 健康检查

| 端点 | 语义 | 是否检查数据库 |
| --- | --- | --- |
| `live` | 进程是否仍能运行 | 否 |
| `ready` | 是否可以接收新流量 | 是，短超时轻量 `SELECT 1` |
| `startup` | 冷启动与初始化是否完成 | 按部署环境决定 |

Liveness 不能依赖数据库，否则数据库短暂故障会触发所有应用副本重启。健康响应不暴露数据库地址、版本、堆栈或环境变量；详细诊断只允许内网或鉴权访问。

高频 Readiness 的 `SELECT 1` 只证明当前可建立轻量连接。应用应在 Startup 阶段一次性校验发布清单中的预期迁移、关键表和必要约束；不要把昂贵 Schema 检查放进每次探测。

### 17.5 优雅关闭

容器平台的推荐终止流程：

1. `preStop`、排空端点或平台等价机制先让实例退出 Ready 状态。
2. 预留 Service / Ingress 端点传播时间，再由平台发送 `SIGTERM`。
3. 应用停止领取新 HTTP 请求、消息和定时任务。
4. 在限定时间内排空请求和在途任务，或安全释放任务租约。
5. 关闭消息、缓存和 Prisma 连接。
6. 正常退出；超时后由平台终止。

Kubernetes 的 `terminationGracePeriodSeconds` 必须大于“端点传播等待 + 最长允许处理时间 + 资源关闭余量”。

### 17.6 SLO 与告警

先定义可用性、延迟、业务成功率、消息积压和恢复目标，再建立告警。告警应以用户影响和错误预算为中心，每个生产告警关联仪表盘和 Runbook，避免只因 CPU 瞬时升高就告警。

基础设施告警至少覆盖 Migration Job 失败、连接池耗尽、WAL 归档中断、最近可恢复时间过旧、备份过期、Outbox 最老事件、死信增长和持续死锁。SLO 告警优先使用多窗口错误预算消耗率。

## 18. 容器、部署与灾难恢复

### 18.1 Docker

- 使用多阶段构建；构建阶段执行 `prisma generate` 和 NestJS build。
- 运行镜像只包含生产依赖、生成 Client 和构建产物。
- 基础镜像固定明确版本；关键环境可以固定 Digest。
- 构建与运行的 OS、CPU 架构、OpenSSL 必须与 Prisma 运行要求兼容。
- 使用非 Root 用户和 Exec Form 启动 Node.js。
- 固定 UID / GID，关闭提权，删除无关 Linux capabilities，启用 seccomp 和只读根文件系统；所需临时目录单独提供受控可写挂载。
- 镜像中不包含 `.env`、源码 Secret、测试数据和开发工具。
- 一个容器运行一个应用进程。
- Entrypoint 不执行生产迁移。

### 18.2 Kubernetes / 容器平台

- HTTP API 使用 Deployment；Migration 使用单独一次性 Job。
- 配置 startup、readiness、liveness Probe。
- 根据副本数、可用区和 SLO 配置资源 Requests、Limits、PDB 和拓扑分散；单副本 `minAvailable: 1` 不能阻塞节点维护。
- HPA 最大副本数必须纳入 PostgreSQL 连接预算。
- Worker 与 HTTP 服务分别设置资源和扩缩容策略。
- 使用 NetworkPolicy 和最小权限 ServiceAccount。
- 验证集群 CNI 实际执行 NetworkPolicy，并分别定义最小入口和出口规则。
- Secret 优先由外部 Secret Manager 注入。
- 数据库优先使用支持 PITR、监控、加密和故障切换的托管 PostgreSQL。

### 18.3 备份策略

先定义：

- RPO：最多允许丢失多少数据。
- RTO：故障后最多允许多久恢复服务。

生产 PostgreSQL 推荐同时具备：

- 定期物理基础备份或托管快照。
- 持续 WAL 归档与时间点恢复（PITR）。
- 加密、跨可用区或跨区域、分层保留。
- 使用跨账号或独立安全域、不可变保留、与生产权限隔离的备份存储和删除保护；KMS 恢复权限也要定期验证。
- 持续监控 WAL 归档链和“最近可恢复时间”，不能只监控备份任务成功。
- 按需补充 `pg_dump` 自定义格式作为逻辑恢复或跨版本迁移工具；单表恢复前必须评估外键、Sequence、权限、扩展和跨表一致性，优先先恢复到隔离实例再抽取。

只读副本和流复制不是备份，误删除和错误更新同样会被复制。逻辑备份也不应成为唯一灾备手段。

### 18.4 恢复演练

备份只有成功恢复后才有效。定期在隔离环境验证：

1. PostgreSQL 能从基础备份和 WAL 正常恢复到指定时间点。
2. 根据发布清单核对 Application / Migration Image Digest、最后迁移名和校验和；Prisma 不会自动推导应用版本与迁移集合的对应关系。
3. 核心表执行行数、校验和、对账和业务不变量检查。
4. 验证 Sequence、外键、未验证约束、无效索引、角色 / GRANT、扩展版本、时区和排序规则。
5. 应用能启动，完成 Canary 写入与核心读写冒烟，并测得实际 RPO。
6. 实际恢复与流量切换时间满足 RTO。
7. 恢复步骤不依赖某个员工的个人经验。

破坏性迁移和批量数据操作前，必须确认最近备份、PITR 链和恢复 Runbook 可用。

PITR 通常先恢复到新隔离实例，不是原库上的快速撤销按钮。恢复后先校验，再选择全量切换或选择性修复，并把连接地址切换、应用 / Worker 重连、缓存失效、消息暂停与重放纳入 RTO；回到较早时间点会丢失其后的合法写入，必须显式处理差异。

## 19. AI-Friendly Architecture

为了让人和 AI 都能快速定位、推导和安全修改系统，必须建立以下规则：

- 同一概念只有一个固定位置和一种实现路径。
- 文件名表达业务动作，例如 `create-user.use-case.ts`。
- Use Case 统一使用 `execute(command)` 或团队约定的单一入口。
- 每个模块提供 README，写明职责、数据所有权、不变量、公开 API 和事件。
- 重要取舍用 ADR 记录背景、决策、替代方案和后果。
- 跨模块只允许从 `public-api.ts` 导入，并用静态规则验证。
- 状态迁移使用显式枚举与方法，禁止多个布尔值组成隐式状态机。
- 构造函数、Getter、Mapper 不隐藏数据库、网络或消息副作用。
- 注释解释约束、原因和不变量，不复述语法表面含义。
- 单个类、文件和函数保持单一职责，业务规则优先写成纯函数或领域对象。
- 禁止用动态字符串、反射注册和全局自动改写隐藏关键调用链。
- 生成代码与手写代码目录隔离，禁止手改 Prisma 生成文件。
- 新增架构模式前先说明它解决的重复问题，不能为“以后可能用到”预建抽象。

## 20. 明确禁止的反模式

| 反模式 | 长期方案 |
| --- | --- |
| 巨型 `AppService` / `UsersService` | 按业务动作拆 Use Case |
| Controller、Job、Consumer 随意调用 Prisma | 统一走应用用例与持久化 Adapter |
| Prisma 类型贯穿各层 | Infrastructure Mapper 隔离 |
| DTO、领域实体、数据库模型共用一个类型 | 按边界分别建模 |
| `forwardRef()` 解决循环依赖 | 重划模块或抽取稳定公开契约 |
| `@Global()` 暴露数据库能力 | 显式导入并只在 Infrastructure 使用 |
| 单例 Service 保存当前用户或事务 | 显式上下文与事务端口 |
| 预查询代替唯一约束 | 友好预检查 + 数据库约束兜底 |
| 默认 `onDelete: Cascade` | 按聚合生命周期明确选择 |
| JSON 保存稳定关系 | 正规列、关联表和外键 |
| 隐式全局软删除过滤 | 明确命名的 Repository 方法 |
| 无上限 `findMany`、深 Offset | 强制上限与 Cursor 分页 |
| 循环查询或 `$transaction` 包 N 次查询 | 批量查询、聚合或 DataLoader |
| 事务内调用第三方 API | 提交后调用或 Transactional Outbox |
| 生产执行 `db push` | 审查迁移 + `migrate deploy` |
| 每个副本启动时自动迁移 | 单独 Migration Job |
| SQLite 代替 PostgreSQL 集成测试 | 同版本真实 PostgreSQL |
| Mock Prisma 就宣称数据库已测试 | Repository 集成测试真实约束和并发 |
| Liveness 查询数据库 | Liveness 只检查进程，Readiness 检查依赖 |
| 日志记录 Token、请求体、SQL 参数 | 集中脱敏和最小字段日志 |
| 提高连接数掩盖慢查询 | 查询计划、索引和池预算治理 |
| 只读副本视为备份 | 基础备份 + WAL/PITR + 恢复演练 |
| 长期保留双写、旧列、fallback | 完成切换后立即删除过渡实现 |

## 21. 检查清单

### 21.1 开发设计前

- [ ] 模块职责、数据所有权和公开接口已明确。
- [ ] 请求流、状态流、事务边界和外部副作用已画清。
- [ ] 不变量分别落在 Domain 与数据库约束的正确位置。
- [ ] DTO、Command、Domain、Prisma、Response 的映射边界明确。
- [ ] 并发策略、幂等策略和错误语义明确。
- [ ] 查询形状、分页方式与索引可以从用例推导。
- [ ] 没有为了假想需求引入缓存、消息或复杂抽象。

### 21.2 合并前

- [ ] Controller 未直接访问 Prisma，Prisma 类型未越层。
- [ ] 未引入循环依赖、深度跨模块导入或新的全局 Provider。
- [ ] 没有无上限查询、N+1、长事务和事务内外部调用。
- [ ] 数据库约束、外键、删除策略和索引经过审查。
- [ ] Prisma 迁移 SQL 已检查锁、表重写和数据风险。
- [ ] Domain / Application 单元测试通过。
- [ ] 真实 PostgreSQL 集成测试和关键 E2E 通过。
- [ ] 鉴权、对象级授权、限流和审计已评估。
- [ ] 日志、指标、Trace 不包含敏感数据或高基数标签。
- [ ] 注释说明了新增约束和原因，未留下临时兼容路径。

### 21.3 发布前

- [ ] 完整迁移链可从空数据库执行。
- [ ] 当前生产版本携带生产形状数据的增量迁移已验证。
- [ ] 数据回填任务可重入、可分批、可限速、有检查点。
- [ ] Migration Job 与应用镜像版本唯一对应。
- [ ] 最大副本数下的数据库连接预算充足。
- [ ] PITR 状态、最近备份和恢复演练有效。
- [ ] 前滚修复、应用回滚适用条件和 Runbook 明确。
- [ ] Probe、仪表盘、告警和核心冒烟用例已更新。
- [ ] 生产 Secret、数据库角色和网络权限符合最小权限。

### 21.4 发布后

- [ ] `_prisma_migrations` 状态、发布清单中的迁移名 / 校验和与关键约束均正确。
- [ ] Readiness 与核心冒烟测试通过。
- [ ] 错误率、P95/P99、连接、锁等待、慢查询无异常。
- [ ] 关键业务成功率和 Outbox / Consumer 积压无异常。
- [ ] 本次发布的过渡代码、字段和临时任务已按计划清理。

### 21.5 定期治理

- [ ] 恢复演练满足实际 RPO / RTO。
- [ ] 慢查询、无效索引、膨胀、Autovacuum 定期复核。
- [ ] Secret、数据库和平台权限定期轮换与审查。
- [ ] Node.js、NestJS、Prisma、PostgreSQL 保持受支持版本。
- [ ] 连接池、HPA、存储、WAL 和日志成本容量复核。
- [ ] 模块 README、ADR、OpenAPI 和事件契约与代码一致。

## 22. 当前 `api` 工程的建议落地顺序

当前工程处于初始化阶段，适合直接建立长期架构，不需要保留现有脚手架式结构的兼容层。

### P0：先建立正确边界

1. 将 Prisma generator 收敛到 `prisma-client` + 明确输出目录，并把 generate 纳入构建。
2. 建立强类型配置层，启动时校验数据库、HTTP、安全和连接池参数。
3. 将全局 `PrismaModule` 收敛为显式 `DatabaseModule`；只允许 Infrastructure Adapter 注入 Client。
4. 建立 `bootstrap / platform / modules / shared-kernel` 目录和依赖规则。
5. 以第一个真实业务模块验证 Presentation → Application / Domain，并由 Infrastructure 实现 Application Port 的完整运行链路。
6. 统一 DTO 校验、错误模型、请求 ID、结构化日志和优雅关闭。
7. 设计首批表时同时提交外键、唯一约束、检查约束、索引和删除策略。
8. 建立真实 PostgreSQL 集成测试、从零迁移测试和生产版本增量迁移测试。
9. 启用完整 TypeScript 严格模式，并把现有带 `--fix` 的 lint 脚本拆为只读 `lint:check` 与开发者显式执行的修复命令。

### P1：达到可生产基线

1. 身份认证、Policy 授权、租户边界和审计。
2. OpenAPI、错误码目录和模块 README / ADR。
3. CI 质量门禁、不可变镜像和独立 Migration Job。
4. Metrics、OpenTelemetry、Readiness / Liveness、SLO 与告警。
5. PITR、恢复演练和数据库权限分离。

### 按真实需求启用

- 需要可靠异步副作用时引入 Outbox 和幂等 Consumer。
- 出现已测量的重复读取瓶颈时引入缓存。
- 大量短连接或连接预算不足时评估 PgBouncer。
- 单模块出现明确独立扩缩容或故障隔离需求时再拆服务。

不要预先搭建没有当前用例的消息、缓存、软删除、微服务或通用 Repository 框架。

## 23. 官方参考

- [NestJS 官方文档](https://docs.nestjs.com/)
- [Prisma ORM 官方文档](https://www.prisma.io/docs/orm)
- [Prisma Migrate](https://www.prisma.io/docs/orm/prisma-migrate)
- [Prisma Transactions and batch queries](https://www.prisma.io/docs/orm/prisma-client/queries/transactions)
- [PostgreSQL 当前版本文档](https://www.postgresql.org/docs/current/)
- [RFC 9457：Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457)
- [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/)
- [OpenTelemetry 官方文档](https://opentelemetry.io/docs/)
