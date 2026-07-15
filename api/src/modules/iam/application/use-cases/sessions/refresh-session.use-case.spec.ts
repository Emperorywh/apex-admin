import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { Email } from '../../../domain/account/email';
import { RefreshTokenId, SessionId, UserId } from '../../../domain/account/identifiers';
import { PasswordHash } from '../../../domain/account/password';
import { User, UserRole, UserStatus } from '../../../domain/account/user';
import { AuthSession } from '../../../domain/session/auth-session';
import {
  RefreshToken,
  RefreshTokenStatus,
  TokenHash,
} from '../../../domain/session/refresh-token';
import { IamTransaction, IamUnitOfWork } from '../../ports/persistence.ports';
import {
  AccessTokenService,
  IamSessionConfig,
  OpaqueTokenService,
  SignedAccessToken,
} from '../../ports/security.ports';
import { RefreshSessionUseCase } from './refresh-session.use-case';
import { RefreshSessionPolicy } from '../../policies/refresh-session.policy';

/*
 * Refresh 应用测试验证宽限与重放分支，并确保错误在事务提交后抛出。
 * 所有时间、随机值、JWT 与 UoW 都使用确定性 Fake。
 */
describe('RefreshSessionUseCase', () => {
  it('宽限期内返回 STALE 且不修改 Session', async () => {
    const scenario = createScenario(
      RefreshTokenStatus.ROTATED,
      new Date('2026-07-14T00:00:07.000Z'),
    );
    await expect(scenario.useCase.execute(command)).rejects.toMatchObject({
      code: 'REFRESH_TOKEN_STALE',
    });
    expect(scenario.events).toEqual(['lock', 'commit']);
    expect(scenario.session.snapshot.status).toBe('ACTIVE');
  });

  it('宽限期外先提交吊销和审计，再返回 REPLAY', async () => {
    const scenario = createScenario(
      RefreshTokenStatus.ROTATED,
      new Date('2026-07-14T00:00:04.000Z'),
    );
    await expect(scenario.useCase.execute(command)).rejects.toMatchObject({
      code: 'REFRESH_TOKEN_REPLAY',
    });
    expect(scenario.events).toEqual([
      'lock',
      'save-session',
      'audit-session',
      'audit-replay',
      'commit',
    ]);
    expect(scenario.session.snapshot).toMatchObject({
      status: 'REVOKED',
      revocationReason: 'REFRESH_TOKEN_REPLAY',
    });
  });
});

const command = { refreshToken: 'old-token', correlationId: 'test-correlation' };
const NOW = new Date('2026-07-14T00:00:10.000Z');

function createScenario(status: RefreshTokenStatus, rotatedAt: Date | null) {
  const events: string[] = [];
  const user = User.restore({
    id: UserId.from('019f5fdf-100f-7c10-9748-6dc673e0b101'),
    email: Email.restore('alice@apex.local'),
    passwordHash: PasswordHash.restore('$argon2id$test'),
    role: UserRole.ADMIN,
    status: UserStatus.ACTIVE,
    createdAt: new Date('2026-07-01T00:00:00.000Z'),
    updatedAt: new Date('2026-07-01T00:00:00.000Z'),
  });
  const session = AuthSession.create({
    id: SessionId.from('019f5fdf-100f-7c10-9748-6dc673e0b102'),
    userId: user.snapshot.id,
    now: new Date('2026-07-01T00:00:00.000Z'),
    expiresAt: new Date('2026-07-21T00:00:00.000Z'),
  });
  const token = RefreshToken.restore({
    id: RefreshTokenId.from('019f5fdf-100f-7c10-9748-6dc673e0b103'),
    sessionId: session.snapshot.id,
    tokenHash: TokenHash.restore('a'.repeat(64)),
    status,
    rotatedAt,
    revokedAt: null,
    createdAt: new Date('2026-07-01T00:00:00.000Z'),
  });
  const transaction = {
    refreshTokens: {
      lockContextByTokenHash: () => {
        events.push('lock');
        return Promise.resolve({ user, session, token });
      },
    },
    sessions: {
      save: () => {
        events.push('save-session');
        return Promise.resolve();
      },
    },
    securityAudit: {
      appendSessionRevoked: () => {
        events.push('audit-session');
        return Promise.resolve();
      },
      appendRefreshReplayDetected: () => {
        events.push('audit-replay');
        return Promise.resolve();
      },
    },
  } as unknown as IamTransaction;
  const useCase = new RefreshSessionUseCase(
    new FakeOpaqueTokens(),
    new FakeAccessTokens(),
    new FakeSessionConfig(),
    new RefreshSessionPolicy(),
    new SequenceIds(),
    new FixedClock(),
    new CommitRecordingUnitOfWork(transaction, events),
  );
  return { useCase, events, session };
}

class FakeOpaqueTokens extends OpaqueTokenService {
  generate() {
    return { value: 'new-token', hash: 'b'.repeat(64) };
  }

  hash(): string {
    return 'a'.repeat(64);
  }
}

class FakeAccessTokens extends AccessTokenService {
  sign(): SignedAccessToken {
    return { value: 'access', expiresAt: new Date(0), expiresInSeconds: 86400 };
  }

  verify(): never {
    throw new Error('测试不调用 verify');
  }
}

class FakeSessionConfig extends IamSessionConfig {
  readonly sessionTtlSeconds = 604800;
  readonly refreshReuseGraceSeconds = 5;
}

class FixedClock extends Clock {
  now(): Date {
    return NOW;
  }
}

class SequenceIds extends IdGenerator {
  private value = 3;

  next(): string {
    this.value += 1;
    return `019f5fdf-100f-7c10-9748-6dc673e0b1${this.value.toString(16).padStart(2, '0')}`;
  }
}

class CommitRecordingUnitOfWork extends IamUnitOfWork {
  constructor(
    private readonly transaction: IamTransaction,
    private readonly events: string[],
  ) {
    super();
  }

  async run<T>(work: (transaction: IamTransaction) => Promise<T>): Promise<T> {
    const result = await work(this.transaction);
    this.events.push('commit');
    return result;
  }

  runSerializable<T>(work: (transaction: IamTransaction) => Promise<T>): Promise<T> {
    return this.run(work);
  }
}
