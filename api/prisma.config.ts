import 'dotenv/config';
import { defineConfig } from 'prisma/config';
import { loadMigrationConfig } from './src/platform/config/migration-config';

/*
 * Prisma CLI 只读取迁移进程需要的连接信息。
 * Runtime 与 Seed 的环境变量不会在此处形成隐式依赖。
 */
const migrationConfig = loadMigrationConfig(process.env);

export default defineConfig({
  schema: 'prisma/schema.prisma',
  migrations: {
    path: 'prisma/migrations',
  },
  datasource: {
    url: migrationConfig.databaseUrl,
  },
});
