import { Inject, Injectable } from '@nestjs/common';
import { Prisma, PrismaClient } from '@prisma/client';
import { DatabaseClient } from '../../../../../platform/database/database-client';
import { DatabaseTransactionConfig } from '../../../../../platform/database/database-transaction-config';
import {
  IamTransaction,
  IamUnitOfWork,
} from '../../../application/ports/persistence.ports';
import { PrismaAuthSessionRepository } from './prisma-session.repository';
import { PrismaRefreshTokenRepository } from './prisma-refresh-token.repository';
import { PrismaSecurityAuditRepository } from './prisma-security-audit.repository';
import { PrismaUserRepository } from './prisma-user.repository';
import {
  isRetryableTransactionError,
  mapConcurrentPersistenceError,
} from './prisma-persistence-errors';

const SERIALIZABLE_MAX_ATTEMPTS = 3;
const RETRY_BASE_DELAY_MS = 20;
const RETRY_JITTER_BOUND_MS = 20;

/*
 * Unit of Work 为每次回调构建绑定同一 tx 的完整 IAM 仓储集合。
 * 普通与 Serializable 入口复用相同超时、锁配置和上下文构造逻辑。
 */
@Injectable()
export class PrismaIamUnitOfWork extends IamUnitOfWork {
  constructor(
    @Inject(DatabaseClient)
    private readonly database: PrismaClient,
    private readonly config: DatabaseTransactionConfig,
  ) {
    super();
  }

  async run<T>(work: (transaction: IamTransaction) => Promise<T>): Promise<T> {
    try {
      return await this.runTransaction(work);
    } catch (error) {
      if (isRetryableTransactionError(error)) mapConcurrentPersistenceError(error);
      throw error;
    }
  }

  async runSerializable<T>(
    work: (transaction: IamTransaction) => Promise<T>,
  ): Promise<T> {
    for (let attempt = 0; attempt < SERIALIZABLE_MAX_ATTEMPTS; attempt += 1) {
      try {
        return await this.runTransaction(work, Prisma.TransactionIsolationLevel.Serializable);
      } catch (error) {
        if (!isRetryableTransactionError(error)) throw error;
        if (attempt === SERIALIZABLE_MAX_ATTEMPTS - 1) {
          mapConcurrentPersistenceError(error);
        }
        await this.delayWithJitter(attempt);
      }
    }
    throw new Error('Serializable 重试状态不可达');
  }

  private runTransaction<T>(
    work: (transaction: IamTransaction) => Promise<T>,
    isolationLevel?: Prisma.TransactionIsolationLevel,
  ): Promise<T> {
    const options = {
      maxWait: this.config.maxWaitMs,
      timeout: this.config.timeoutMs,
      ...(isolationLevel ? { isolationLevel } : {}),
    };
    return this.database.$transaction(async (tx) => {
      await tx.$queryRaw(
        Prisma.sql`
          SELECT
            set_config('statement_timeout', ${`${this.config.statementTimeoutMs}ms`}, true),
            set_config('lock_timeout', ${`${this.config.lockTimeoutMs}ms`}, true)
        `,
      );
      return work(this.createTransaction(tx));
    }, options);
  }

  private createTransaction(tx: Prisma.TransactionClient): IamTransaction {
    return {
      users: new PrismaUserRepository(tx),
      sessions: new PrismaAuthSessionRepository(tx),
      refreshTokens: new PrismaRefreshTokenRepository(tx),
      securityAudit: new PrismaSecurityAuditRepository(tx),
    };
  }

  private delayWithJitter(attempt: number): Promise<void> {
    const baseDelayMs = RETRY_BASE_DELAY_MS * 2 ** attempt;
    const jitterMs = Math.floor(Math.random() * RETRY_JITTER_BOUND_MS);
    return new Promise((resolve) => setTimeout(resolve, baseDelayMs + jitterMs));
  }
}
