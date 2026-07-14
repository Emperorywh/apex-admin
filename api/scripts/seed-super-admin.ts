import 'dotenv/config';
import { randomUUID } from 'node:crypto';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '@prisma/client';
import { loadSeedConfig } from '../src/platform/config/seed-config';
import { DatabaseTransactionConfig } from '../src/platform/database/database-transaction-config';
import { SystemClock } from '../src/shared/kernel/clock';
import { UuidV7Generator } from '../src/shared/kernel/id-generator';
import {
  BootstrapSuperAdminConflictError,
  BootstrapSuperAdminUseCase,
} from '../src/modules/iam/application/use-cases/accounts/bootstrap-super-admin.use-case';
import {
  PasswordHashingConfig,
} from '../src/modules/iam/application/ports/security.ports';
import { IamError } from '../src/modules/iam/application/errors/iam.error';
import { Argon2PasswordHasher } from '../src/modules/iam/infrastructure/crypto/argon2-password-hasher';
import { LocalPasswordBlocklist } from '../src/modules/iam/infrastructure/crypto/local-password-blocklist';
import { PrismaIamUnitOfWork } from '../src/modules/iam/infrastructure/persistence/prisma/prisma-iam.unit-of-work';

/*
 * 独立 CLI Adapter 只完成 Seed 配置、基础设施组装与用例调用。
 * 它不复制邮箱规范化、密码策略、幂等状态表或 Prisma upsert。
 */
class SeedPasswordHashingConfig extends PasswordHashingConfig {
  constructor(
    readonly memoryKiB: number,
    readonly timeCost: number,
    readonly parallelism: number,
    readonly maxConcurrency: number,
  ) {
    super();
  }
}

async function main(): Promise<void> {
  const config = loadSeedConfig(process.env);
  const database = new PrismaClient({
    adapter: new PrismaPg({ connectionString: config.databaseUrl, max: 2 }),
  });
  const transactionConfig: DatabaseTransactionConfig = {
    maxWaitMs: 2000,
    timeoutMs: 5000,
    statementTimeoutMs: 4000,
    lockTimeoutMs: 1500,
  };
  const ids = new UuidV7Generator();
  const passwordConfig = new SeedPasswordHashingConfig(
    config.passwordHashing.memoryKiB,
    config.passwordHashing.timeCost,
    config.passwordHashing.parallelism,
    config.passwordHashing.maxConcurrency,
  );
  const useCase = new BootstrapSuperAdminUseCase(
    new LocalPasswordBlocklist(),
    new Argon2PasswordHasher(passwordConfig),
    ids,
    new SystemClock(),
    new PrismaIamUnitOfWork(database, transactionConfig),
  );

  try {
    await database.$connect();
    const result = await useCase.execute({
      email: config.email,
      password: config.password,
      correlationId: randomUUID(),
    });
    process.stdout.write(
      `${JSON.stringify({ userId: result.userId, email: result.email, created: result.created })}\n`,
    );
  } finally {
    await database.$disconnect();
  }
}

void main().catch((error: unknown) => {
  const safeMessage =
    error instanceof BootstrapSuperAdminConflictError || error instanceof IamError
      ? error.message
      : '内部错误';
  process.stderr.write(`SUPER_ADMIN bootstrap 失败：${safeMessage}\n`);
  process.exitCode = 1;
});
