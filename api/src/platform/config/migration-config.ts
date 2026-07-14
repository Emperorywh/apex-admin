import { z } from 'zod';

/*
 * Migration 配置只服务 Prisma CLI 与部署迁移 Job。
 * 它不接受 Runtime 连接串作为静默 fallback。
 */
const migrationEnvironmentSchema = z.object({
  MIGRATION_DATABASE_URL: z
    .string()
    .url()
    .refine(
      (value) => value.startsWith('postgresql://') || value.startsWith('postgres://'),
      'MIGRATION_DATABASE_URL 必须是 PostgreSQL 连接地址',
    ),
});

export interface MigrationConfig {
  readonly databaseUrl: string;
}

export function loadMigrationConfig(environment: NodeJS.ProcessEnv): MigrationConfig {
  const parsed = migrationEnvironmentSchema.parse(environment);
  return Object.freeze({ databaseUrl: parsed.MIGRATION_DATABASE_URL });
}
