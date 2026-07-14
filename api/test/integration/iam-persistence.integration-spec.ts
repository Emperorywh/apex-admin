import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '@prisma/client';
import { StartedPostgreSqlContainer } from '@testcontainers/postgresql';
import { Email } from '../../src/modules/iam/domain/account/email';
import { UserId } from '../../src/modules/iam/domain/account/identifiers';
import { PasswordHash } from '../../src/modules/iam/domain/account/password';
import { User, UserRole } from '../../src/modules/iam/domain/account/user';
import { PrismaUserRepository } from '../../src/modules/iam/infrastructure/persistence/prisma/prisma-user.repository';
import { startMigratedPostgres } from '../support/postgres-test-environment';

/*
 * IAM 持久化集成测试在真实 PostgreSQL 上验证迁移、CHECK、唯一索引与 Mapper。
 * 每个测试清空自身容器数据库，不与开发或生产数据共享状态。
 */
describe('IAM PostgreSQL 持久化约束', () => {
  let container: StartedPostgreSqlContainer;
  let database: PrismaClient;

  beforeAll(async () => {
    const environment = await startMigratedPostgres();
    container = environment.container;
    database = new PrismaClient({
      adapter: new PrismaPg({ connectionString: environment.databaseUrl, max: 4 }),
    });
    await database.$connect();
  }, 120_000);

  afterEach(async () => {
    await database.securityAuditEvent.deleteMany();
    await database.refreshToken.deleteMany();
    await database.authSession.deleteMany();
    await database.user.deleteMany();
  });

  afterAll(async () => {
    await database.$disconnect();
    await container.stop();
  });

  it('从空库完成迁移并拒绝非 canonical email', async () => {
    await expect(
      database.user.create({
        data: {
          id: id(1),
          email: 'Alice@apex.local',
          passwordHash: '$argon2id$test',
          role: 'VIEWER',
          updatedAt: new Date(),
        },
      }),
    ).rejects.toBeDefined();
  });

  it('约束 Session 撤销状态与元数据必须一致', async () => {
    await createUserRecord(database, id(2));
    await expect(
      database.authSession.create({
        data: {
          id: id(3),
          userId: id(2),
          status: 'REVOKED',
          expiresAt: new Date(Date.now() + 86_400_000),
        },
      }),
    ).rejects.toBeDefined();
  });

  it('每个 Session 最多存在一个 ACTIVE Refresh Token', async () => {
    await createUserRecord(database, id(4));
    await database.authSession.create({
      data: {
        id: id(5),
        userId: id(4),
        expiresAt: new Date(Date.now() + 86_400_000),
      },
    });
    await database.refreshToken.create({
      data: {
        id: id(6),
        sessionId: id(5),
        tokenHash: 'a'.repeat(64),
      },
    });
    await expect(
      database.refreshToken.create({
        data: {
          id: id(7),
          sessionId: id(5),
          tokenHash: 'b'.repeat(64),
        },
      }),
    ).rejects.toMatchObject({ code: 'P2002' });
  });

  it('Prisma Mapper 完成 User 聚合往返', async () => {
    const repository = new PrismaUserRepository(database);
    const now = new Date();
    const user = User.create({
      id: UserId.from(id(8)),
      email: Email.create('Mapper@Apex.Local'),
      passwordHash: PasswordHash.restore('$argon2id$test'),
      role: UserRole.OPERATOR,
      now,
    });
    await repository.add(user);
    const restored = await repository.findByEmail(Email.create('mapper@apex.local'));
    expect(restored?.snapshot).toMatchObject({
      id: user.snapshot.id,
      email: user.snapshot.email,
      role: UserRole.OPERATOR,
    });
  });
});

function id(suffix: number): string {
  return `019f5fdf-100f-7c10-9748-6dc673e0b1${suffix.toString(16).padStart(2, '0')}`;
}

async function createUserRecord(database: PrismaClient, userId: string): Promise<void> {
  await database.user.create({
    data: {
      id: userId,
      email: `${userId.slice(-4)}@apex.local`,
      passwordHash: '$argon2id$test',
      role: 'VIEWER',
      updatedAt: new Date(),
    },
  });
}
