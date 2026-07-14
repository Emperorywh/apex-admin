import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { PasswordHash } from '../../../domain/account/password';
import { UserRole } from '../../../domain/account/user';
import { ROLE_PERMISSIONS } from '../../../domain/authorization/authorization';
import { AuthenticatedActor } from '../../contracts/authenticated-actor';
import { UserPolicy } from '../../policies/user.policy';
import { IamTransaction, IamUnitOfWork } from '../../ports/persistence.ports';
import { PasswordBlocklist, PasswordHasher } from '../../ports/security.ports';
import { CreateUserUseCase } from './create-user.use-case';

/*
 * 创建用户应用测试验证无权请求不会消耗密码哈希，并固定编排顺序。
 * 时钟、ID、Hasher 与 UoW 全部使用确定性 Fake/Stub。
 */
describe('CreateUserUseCase', () => {
  const actor = (role: UserRole): AuthenticatedActor => ({
    id: '019f5fdf-100f-7c10-9748-6dc673e0b100',
    sessionId: '019f5fdf-100f-7c10-9748-6dc673e0b101',
    role,
    permissions: ROLE_PERMISSIONS[role],
    issuedAt: new Date(0),
    expiresAt: new Date(1),
  });

  it('先授权，再执行密码策略和 UoW', async () => {
    const events: string[] = [];
    const transaction = {
      users: {
        findByEmail: () => {
          events.push('query');
          return Promise.resolve(null);
        },
        add: () => {
          events.push('add');
          return Promise.resolve();
        },
      },
      securityAudit: {
        appendUserCreated: () => {
          events.push('audit');
          return Promise.resolve();
        },
      },
    } as unknown as IamTransaction;
    const useCase = new CreateUserUseCase(
      new RecordingPolicy(events),
      new RecordingBlocklist(events),
      new RecordingHasher(events),
      new SequenceIds(),
      new FixedClock(),
      new RecordingUnitOfWork(events, transaction),
    );

    await useCase.execute({
      actor: actor(UserRole.OPERATOR),
      email: 'viewer@apex.local',
      password: 'violet-cabin-echo-planet-4729',
      role: UserRole.VIEWER,
      correlationId: 'test-correlation',
    });
    expect(events).toEqual(['policy', 'blocklist', 'hash', 'uow', 'query', 'add', 'audit']);
  });

  it('无权创建同级角色时不执行高成本操作', async () => {
    const events: string[] = [];
    const useCase = new CreateUserUseCase(
      new RecordingPolicy(events),
      new RecordingBlocklist(events),
      new RecordingHasher(events),
      new SequenceIds(),
      new FixedClock(),
      new RecordingUnitOfWork(events, {} as IamTransaction),
    );
    await expect(
      useCase.execute({
        actor: actor(UserRole.OPERATOR),
        email: 'operator@apex.local',
        password: 'violet-cabin-echo-planet-4729',
        role: UserRole.OPERATOR,
        correlationId: 'test-correlation',
      }),
    ).rejects.toMatchObject({ code: 'INSUFFICIENT_PRIVILEGE' });
    expect(events).toEqual(['policy']);
  });
});

class RecordingPolicy extends UserPolicy {
  constructor(private readonly events: string[]) {
    super();
  }

  override canCreate(actor: AuthenticatedActor, requestedRole: UserRole): boolean {
    this.events.push('policy');
    return super.canCreate(actor, requestedRole);
  }
}

class RecordingBlocklist extends PasswordBlocklist {
  constructor(private readonly events: string[]) {
    super();
  }

  contains(): boolean {
    this.events.push('blocklist');
    return false;
  }
}

class RecordingHasher extends PasswordHasher {
  constructor(private readonly events: string[]) {
    super();
  }

  hash(): Promise<PasswordHash> {
    this.events.push('hash');
    return Promise.resolve(PasswordHash.restore('$argon2id$test'));
  }

  verify(): Promise<boolean> {
    return Promise.resolve(true);
  }
}

class SequenceIds extends IdGenerator {
  private sequence = 0;

  next(): string {
    this.sequence += 1;
    return `019f5fdf-100f-7c10-9748-6dc673e0b1${this.sequence.toString(16).padStart(2, '0')}`;
  }
}

class FixedClock extends Clock {
  now(): Date {
    return new Date('2026-07-14T00:00:00.000Z');
  }
}

class RecordingUnitOfWork extends IamUnitOfWork {
  constructor(
    private readonly events: string[],
    private readonly transaction: IamTransaction,
  ) {
    super();
  }

  run<T>(work: (transaction: IamTransaction) => Promise<T>): Promise<T> {
    this.events.push('uow');
    return work(this.transaction);
  }

  runSerializable<T>(work: (transaction: IamTransaction) => Promise<T>): Promise<T> {
    return work(this.transaction);
  }
}
