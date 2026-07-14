import { loadMigrationConfig } from './migration-config';
import { loadRuntimeConfig } from './runtime-config';
import { loadSeedConfig } from './seed-config';

/*
 * 配置测试固定 Runtime、Migration、Seed 三个独立进程入口的失败关闭边界。
 * 测试使用非敏感本地占位值，不读取开发 .env。
 */
describe('分入口配置', () => {
  const runtime = {
    NODE_ENV: 'test',
    DATABASE_URL: 'postgresql://runtime:test@localhost:5432/apex',
    JWT_ACCESS_SECRET_BASE64: Buffer.alloc(32, 1).toString('base64'),
    CORS_ORIGINS: 'https://admin.apex.local',
  };

  it('Runtime 解析强类型默认值且不需要 Migration/Seed Secret', () => {
    expect(loadRuntimeConfig(runtime)).toMatchObject({
      http: { port: 3000 },
      jwt: { accessTtlSeconds: 900 },
      session: { ttlSeconds: 604800 },
    });
  });

  it('生产环境拒绝非 Secure Cookie 和短 JWT 密钥', () => {
    expect(() => loadRuntimeConfig({ ...runtime, NODE_ENV: 'production' })).toThrow();
    expect(() =>
      loadRuntimeConfig({
        ...runtime,
        JWT_ACCESS_SECRET_BASE64: Buffer.alloc(31, 1).toString('base64'),
      }),
    ).toThrow();
  });

  it('Migration 不回退读取 DATABASE_URL', () => {
    expect(() => loadMigrationConfig({ DATABASE_URL: runtime.DATABASE_URL })).toThrow();
    expect(
      loadMigrationConfig({ MIGRATION_DATABASE_URL: runtime.DATABASE_URL }),
    ).toEqual({ databaseUrl: runtime.DATABASE_URL });
  });

  it('Seed 不要求 JWT、CORS 或 Migration Secret', () => {
    expect(
      loadSeedConfig({
        DATABASE_URL: runtime.DATABASE_URL,
        SUPER_ADMIN_EMAIL: 'admin@apex.local',
        SUPER_ADMIN_PASSWORD: 'violet-cabin-echo-planet-4729',
      }),
    ).toMatchObject({ email: 'admin@apex.local' });
  });
});
