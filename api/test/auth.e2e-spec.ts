import { createHash } from 'node:crypto';
import { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '@prisma/client';
import { StartedPostgreSqlContainer } from '@testcontainers/postgresql';
import { argon2id, hash } from 'argon2';
import request from 'supertest';
import { App } from 'supertest/types';
import { setupApplication } from '../src/bootstrap/setup-application';
import { RuntimeConfig } from '../src/platform/config/runtime-config';
import { startMigratedPostgres } from './support/postgres-test-environment';

/*
 * HTTP E2E 在真实 PostgreSQL 上覆盖认证、Cookie、Problem Details 与对象授权主链路。
 * 测试直接调用 Nest HTTP Server，不启动或控制任何浏览器。
 */
describe('SPEC-0001 Auth/RBAC HTTP E2E', () => {
  const origin = 'https://admin.apex.local';
  const adminEmail = 'admin@apex.local';
  const adminPassword = 'violet-cabin-echo-planet-4729';
  let container: StartedPostgreSqlContainer;
  let database: PrismaClient;
  let app: INestApplication<App>;

  beforeAll(async () => {
    const environment = await startMigratedPostgres();
    container = environment.container;
    configureRuntimeEnvironment(environment.databaseUrl, origin);
    database = new PrismaClient({
      adapter: new PrismaPg({ connectionString: environment.databaseUrl, max: 4 }),
    });
    await database.$connect();
    await seedAdmin(database, adminEmail, adminPassword);

    const { AppModule } = await import('../src/app.module.js');
    const module = await Test.createTestingModule({ imports: [AppModule] }).compile();
    app = module.createNestApplication();
    setupApplication(app, app.get(RuntimeConfig));
    await app.init();
  }, 120_000);

  afterAll(async () => {
    await app.close();
    await database.$disconnect();
    await container.stop();
  });

  it('默认拒绝未认证的受保护端点', async () => {
    const response = await request(app.getHttpServer()).get('/v1/users').expect(401);
    expect(readBody(response.body)).toMatchObject({ code: 'ACCESS_TOKEN_INVALID' });
    expect(response.headers['content-type']).toContain('application/problem+json');
  });

  it('Public 登录仍要求可信 Origin 与严格 DTO', async () => {
    await request(app.getHttpServer())
      .post('/v1/auth/login')
      .send({ email: adminEmail, password: adminPassword })
      .expect(403)
      .expect(({ body }) => {
        expect(readBody(body)).toMatchObject({ code: 'UNTRUSTED_ORIGIN' });
      });

    await request(app.getHttpServer())
      .post('/v1/auth/login')
      .set('Origin', origin)
      .send({ email: adminEmail, password: adminPassword, unknown: true })
      .expect(400)
      .expect(({ body }) => {
        const problem = readBody(body);
        expect(problem['code']).toBe('VALIDATION_FAILED');
        expect(Array.isArray(problem['errors'])).toBe(true);
      });
  });

  it('登录、me、刷新宽限与登出符合双 Token 契约', async () => {
    const login = await loginAs(adminEmail, adminPassword);
    expect(login.setCookie).toContain('HttpOnly');
    expect(login.setCookie).toContain('SameSite=Lax');
    expect(login.setCookie).toContain('Path=/v1/auth');
    expect(login.setCookie).not.toContain('Domain=');

    await request(app.getHttpServer())
      .get('/v1/auth/me')
      .set('Authorization', `Bearer ${login.accessToken}`)
      .expect(200)
      .expect(({ body }) => {
        expect(readBody(body)).toMatchObject({
          data: {
            user: { email: adminEmail, role: 'SUPER_ADMIN', status: 'ACTIVE' },
            authorization: { stale: false },
          },
        });
      });

    const rotated = await request(app.getHttpServer())
      .post('/v1/auth/refresh')
      .set('Origin', origin)
      .set('Cookie', login.cookieHeader)
      .expect(200);
    expect(readSetCookie(rotated.headers)).toContain('refresh_token=');

    const stale = await request(app.getHttpServer())
      .post('/v1/auth/refresh')
      .set('Origin', origin)
      .set('Cookie', login.cookieHeader)
      .expect(409);
    expect(readBody(stale.body)).toMatchObject({ code: 'REFRESH_TOKEN_STALE' });
    expect(stale.headers['set-cookie']).toBeUndefined();

    const logout = await request(app.getHttpServer())
      .post('/v1/auth/logout')
      .set('Origin', origin)
      .set('Cookie', login.cookieHeader)
      .expect(204);
    expect(readSetCookie(logout.headers)).toContain('Path=/v1/auth');
  });

  it('宽限期外旧 refresh 重用会持久化吊销后返回 REPLAY', async () => {
    const login = await loginAs(adminEmail, adminPassword);
    await request(app.getHttpServer())
      .post('/v1/auth/refresh')
      .set('Origin', origin)
      .set('Cookie', login.cookieHeader)
      .expect(200);

    const oldToken = login.cookieHeader.split('=')[1]!;
    const oldHash = createHash('sha256').update(oldToken).digest('hex');
    await database.refreshToken.update({
      where: { tokenHash: oldHash },
      data: { rotatedAt: new Date(Date.now() - 10_000) },
    });
    await request(app.getHttpServer())
      .post('/v1/auth/refresh')
      .set('Origin', origin)
      .set('Cookie', login.cookieHeader)
      .expect(401)
      .expect(({ body }) => {
        expect(readBody(body)).toMatchObject({ code: 'REFRESH_TOKEN_REPLAY' });
      });

    const persisted = await database.refreshToken.findUniqueOrThrow({
      where: { tokenHash: oldHash },
      include: { session: true },
    });
    expect(persisted.session).toMatchObject({
      status: 'REVOKED',
      revocationReason: 'REFRESH_TOKEN_REPLAY',
    });
    expect(
      await database.securityAuditEvent.count({
        where: {
          sessionId: persisted.sessionId,
          action: 'REFRESH_REPLAY_DETECTED',
        },
      }),
    ).toBe(1);
  });

  it('OPERATOR 只能创建 VIEWER，不能创建同级角色', async () => {
    const admin = await loginAs(adminEmail, adminPassword);
    const operatorEmail = 'operator@apex.local';
    const operatorPassword = 'maple-orbit-kiln-velvet-8306';
    const createdOperator = await request(app.getHttpServer())
      .post('/v1/users')
      .set('Authorization', `Bearer ${admin.accessToken}`)
      .send({ email: operatorEmail, password: operatorPassword, role: 'OPERATOR' })
      .expect(201);
    const operatorId = readBody(readBody(createdOperator.body)['data'])['id'];
    if (typeof operatorId !== 'string') throw new Error('创建用户响应缺少 id');

    const operator = await loginAs(operatorEmail, operatorPassword);
    await request(app.getHttpServer())
      .post('/v1/users')
      .set('Authorization', `Bearer ${operator.accessToken}`)
      .send({
        email: 'viewer@apex.local',
        password: 'harbor-lantern-velvet-9426',
        role: 'VIEWER',
      })
      .expect(201);
    await request(app.getHttpServer())
      .post('/v1/users')
      .set('Authorization', `Bearer ${operator.accessToken}`)
      .send({
        email: 'operator2@apex.local',
        password: 'cedar-meadow-orbit-4175',
        role: 'OPERATOR',
      })
      .expect(403)
      .expect(({ body }) => {
        expect(readBody(body)).toMatchObject({ code: 'INSUFFICIENT_PRIVILEGE' });
      });

    await request(app.getHttpServer())
      .post(`/v1/users/${operatorId}/disable`)
      .set('Authorization', `Bearer ${admin.accessToken}`)
      .expect(204);
    await request(app.getHttpServer())
      .post('/v1/auth/refresh')
      .set('Origin', origin)
      .set('Cookie', operator.cookieHeader)
      .expect(401)
      .expect(({ body }) => {
        expect(readBody(body)).toMatchObject({ code: 'USER_DISABLED' });
      });
  });

  async function loginAs(email: string, password: string): Promise<{
    accessToken: string;
    setCookie: string;
    cookieHeader: string;
  }> {
    const response = await request(app.getHttpServer())
      .post('/v1/auth/login')
      .set('Origin', origin)
      .send({ email, password })
      .expect(200);
    const body = readBody(response.body);
    const data = readBody(body['data']);
    const accessToken = data['accessToken'];
    if (typeof accessToken !== 'string') throw new Error('E2E 登录响应缺少 accessToken');
    const setCookie = readSetCookie(response.headers);
    return { accessToken, setCookie, cookieHeader: setCookie.split(';')[0]! };
  }
});

function configureRuntimeEnvironment(databaseUrl: string, origin: string): void {
  Object.assign(process.env, {
    NODE_ENV: 'test',
    DATABASE_URL: databaseUrl,
    JWT_ACCESS_SECRET_BASE64: Buffer.alloc(32, 9).toString('base64'),
    CORS_ORIGINS: origin,
    COOKIE_SECURE: 'false',
    ARGON2_MEMORY_KIB: '19456',
    ARGON2_TIME_COST: '2',
    ARGON2_PARALLELISM: '1',
    ARGON2_MAX_CONCURRENCY: '2',
    RATE_LIMIT_LOGIN_PER_IP: '100',
    RATE_LIMIT_LOGIN_PER_EMAIL: '100',
    REFRESH_REUSE_GRACE_SECONDS: '5',
  });
}

async function seedAdmin(
  database: PrismaClient,
  email: string,
  password: string,
): Promise<void> {
  const passwordHash = await hash(password, {
    type: argon2id,
    memoryCost: 19456,
    timeCost: 2,
    parallelism: 1,
  });
  await database.user.create({
    data: {
      id: '019f5fdf-100f-7c10-9748-6dc673e0b100',
      email,
      passwordHash,
      role: 'SUPER_ADMIN',
      updatedAt: new Date(),
    },
  });
}

function readBody(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('E2E 响应体不是对象');
  }
  return value as Record<string, unknown>;
}

function readSetCookie(headers: Record<string, unknown>): string {
  const value = headers['set-cookie'];
  if (Array.isArray(value) && typeof value[0] === 'string') return value[0];
  if (typeof value === 'string') return value;
  throw new Error('E2E 响应缺少 Set-Cookie');
}
