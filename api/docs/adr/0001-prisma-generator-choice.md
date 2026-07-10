# ADR-0001：Prisma 生成器选择 `prisma-client-js` + 驱动适配器

- 状态：已接受（Accepted）
- 日期：2026-07-10
- 相关文档：[NestJS + Prisma + PostgreSQL 开发最佳实践](../NestJS-Prisma-PostgreSQL-最佳实践.md) §8.1

## 背景（Context）

本项目是 NestJS 11 的 CommonJS 工程（`tsconfig.json` 为 `module: nodenext` / `moduleResolution: nodenext`，`package.json` 未声明 `"type": "module"`），通过 Prisma 7.8 与 `@prisma/adapter-pg` 连接 PostgreSQL。

Prisma 7 带来两个与本项目直接相关的变化：

1. **Schema 不再支持 `url` 字段。** `datasource db { url = env("DATABASE_URL") }` 在 Prisma 7 会触发 `P1012`。连接地址改为由 `prisma.config.ts` 的 `datasource.url`（供 CLI / 迁移）与运行时构造函数注入的驱动适配器（供 Client）分别提供。
2. **默认生成器变更为 `prisma-client`，输出 ESM TypeScript 源码。** 它使用 `import.meta`，生成到 `src/generated/prisma`。

## 决策（Decision）

采用经典 **`prisma-client-js` 生成器 + `@prisma/adapter-pg` 驱动适配器**，而不是 Prisma 7 默认的 `prisma-client` 生成器。

- `schema.prisma` 中 `generator client { provider = "prisma-client-js" }`，不声明 `output`。
- 运行时连接通过 `new PrismaPg({ connectionString })` 适配器注入 `PrismaClient`，不在 Schema 写 `url`。
- CLI 与迁移通过 `prisma.config.ts` 读取连接地址。

## 替代方案（Alternatives Considered）

### 方案 B：使用默认 `prisma-client` 生成器

默认生成器输出 ESM 源码。在本工程的 CJS 构建下会产出 CJS/ESM 混合产物，运行时崩溃：

```
Error: exports is not defined in ES module scope
```

要正确使用默认生成器，必须把整个工程改造为 ESM：给所有相对 import 补 `.js` 后缀，并调整 `tsconfig`、NestJS 构建与 Jest 配置。对于一个脚手架结构尚未稳定、已存在大量相对 import 的工程，这是侵入面过大、收益不匹配的重构。当前没有驱动该改造的业务需求。

### 方案 C：仅迁移连接方式，保持 `prisma-client-js`

连接方式（移除 Schema `url`、改用适配器）是 Prisma 7 的强制要求，与生成器选择无关，本决策已采纳这部分；生成器仍保持 `prisma-client-js`。本方案即最终决策。

## 后果（Consequences）

**正向：**

- 工程保持 CJS，无需立即进行 ESM 全量改造，相对 import 无需加后缀。
- `import { PrismaClient } from '@prisma/client'` 在 Prisma 7.8 仍可用且 CJS 兼容。
- 满足 Prisma 7 对连接方式的强制要求（适配器注入）。

**负向 / 风险：**

- 偏离 Prisma 7 的"新默认"路径。若未来某个 Prisma 主版本停止支持 `prisma-client-js`，需要先完成 ESM 改造再切换。
- 每次升级 Prisma 主版本前，必须确认 `prisma-client-js` 仍受支持。
- 生成的类型仍从 `node_modules/@prisma/client` 导入；架构约束（Prisma 类型不越出 Infrastructure 边界）不受影响，继续按最佳实践 §8.3 由 Mapper 隔离。

## 迁移触发条件

当且仅当满足以下任一条件时，重新评估是否切换到默认 `prisma-client` 生成器：

- 工程因其他原因已经完成 ESM 改造（全量相对 import `.js` 后缀）；
- 或 Prisma 恢复默认生成器输出兼容 CJS 的产物；
- 或 `prisma-client-js` 被官方废弃。

切换时需同步更新 [NestJS + Prisma + PostgreSQL 开发最佳实践](../NestJS-Prisma-PostgreSQL-最佳实践.md) §8.1 与本 ADR。

## 验证

验证数据库连接与生成器可用性：`npx tsx scripts/test-connection.ts`。
