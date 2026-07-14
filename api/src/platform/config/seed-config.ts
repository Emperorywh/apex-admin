import { z } from 'zod';

/*
 * Bootstrap Job 只校验自身真正需要的 Secret 与 Argon2 参数。
 * HTTP、CORS、JWT 和 Migration 配置不会污染该进程入口。
 */
const boundedInteger = (minimum: number, maximum: number, defaultValue: number) =>
  z.coerce.number().int().min(minimum).max(maximum).default(defaultValue);

const seedEnvironmentSchema = z.object({
  DATABASE_URL: z
    .string()
    .url()
    .refine(
      (value) => value.startsWith('postgresql://') || value.startsWith('postgres://'),
      'DATABASE_URL 必须是 PostgreSQL 连接地址',
    ),
  SUPER_ADMIN_EMAIL: z.string().min(1).max(320),
  SUPER_ADMIN_PASSWORD: z.string().min(1),
  ARGON2_MEMORY_KIB: boundedInteger(19456, 1048576, 65536),
  ARGON2_TIME_COST: boundedInteger(2, 20, 3),
  ARGON2_PARALLELISM: boundedInteger(1, 16, 1),
  ARGON2_MAX_CONCURRENCY: boundedInteger(1, 64, 4),
});

export interface SeedConfig {
  readonly databaseUrl: string;
  readonly email: string;
  readonly password: string;
  readonly passwordHashing: Readonly<{
    memoryKiB: number;
    timeCost: number;
    parallelism: number;
    maxConcurrency: number;
  }>;
}

export function loadSeedConfig(environment: NodeJS.ProcessEnv): SeedConfig {
  const parsed = seedEnvironmentSchema.parse(environment);
  return Object.freeze({
    databaseUrl: parsed.DATABASE_URL,
    email: parsed.SUPER_ADMIN_EMAIL,
    password: parsed.SUPER_ADMIN_PASSWORD,
    passwordHashing: Object.freeze({
      memoryKiB: parsed.ARGON2_MEMORY_KIB,
      timeCost: parsed.ARGON2_TIME_COST,
      parallelism: parsed.ARGON2_PARALLELISM,
      maxConcurrency: parsed.ARGON2_MAX_CONCURRENCY,
    }),
  });
}
