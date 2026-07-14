import { spawn } from 'node:child_process';
import {
  PostgreSqlContainer,
  StartedPostgreSqlContainer,
} from '@testcontainers/postgresql';

/*
 * 测试环境始终启动真实 PostgreSQL 17，并执行仓库内完整 migration deploy。
 * 不提供 SQLite、远程数据库或跳过迁移的 fallback 路径。
 */
export async function startMigratedPostgres(): Promise<{
  container: StartedPostgreSqlContainer;
  databaseUrl: string;
}> {
  const container = await new PostgreSqlContainer('postgres:17-alpine')
    .withDatabase('apex_admin_test')
    .withUsername('apex_test')
    .withPassword('local-test-password')
    .start();
  const databaseUrl = container.getConnectionUri();
  try {
    await deployMigrations(databaseUrl);
    return { container, databaseUrl };
  } catch (error) {
    await container.stop();
    throw error;
  }
}

function deployMigrations(databaseUrl: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn('pnpm', ['prisma:migrate:deploy'], {
      cwd: process.cwd(),
      env: { ...process.env, MIGRATION_DATABASE_URL: databaseUrl },
      shell: process.platform === 'win32',
      stdio: 'ignore',
    });
    child.once('error', () => reject(new Error('无法启动 Prisma Migration Job')));
    child.once('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Prisma Migration Job 失败，退出码 ${code ?? 'unknown'}`));
    });
  });
}
