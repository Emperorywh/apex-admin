import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { Email } from '../../../domain/account/email';
import { UserId } from '../../../domain/account/identifiers';
import { NewPassword } from '../../../domain/account/password';
import { User, UserRole, UserStatus } from '../../../domain/account/user';
import { DomainInvariantError } from '../../../domain/errors/domain-invariant.error';
import { IamError, IamErrors } from '../../errors/iam.error';
import { IamUnitOfWork } from '../../ports/persistence.ports';
import { PasswordBlocklist, PasswordHasher } from '../../ports/security.ports';

/*
 * Bootstrap 复用正式邮箱、密码、事务与审计边界，并按现有账号状态失败关闭。
 * CLI 只调用该用例，不复制 Prisma upsert 或安全策略。
 */
export class BootstrapSuperAdminConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BootstrapSuperAdminConflictError';
  }
}

export interface BootstrapSuperAdminResult {
  readonly userId: string;
  readonly email: string;
  readonly created: boolean;
}

@Injectable()
export class BootstrapSuperAdminUseCase {
  constructor(
    private readonly blocklist: PasswordBlocklist,
    private readonly passwordHasher: PasswordHasher,
    private readonly ids: IdGenerator,
    private readonly clock: Clock,
    private readonly unitOfWork: IamUnitOfWork,
  ) {}

  async execute(command: {
    email: string;
    password: string;
    correlationId: string;
  }): Promise<BootstrapSuperAdminResult> {
    const email = Email.create(command.email);
    let password: NewPassword;
    try {
      password = NewPassword.create(command.password);
    } catch (error) {
      if (error instanceof DomainInvariantError) throw IamErrors.passwordNotAllowed();
      throw error;
    }
    if (this.blocklist.contains(password.value, email.value)) {
      throw IamErrors.passwordNotAllowed();
    }

    const passwordHash = await this.passwordHasher.hash(password.value);
    const now = this.clock.now();
    const user = User.create({
      id: UserId.from(this.ids.next()),
      email,
      passwordHash,
      role: UserRole.SUPER_ADMIN,
      now,
    });
    const auditId = this.ids.next();

    try {
      return await this.unitOfWork.run(async ({ users, securityAudit }) => {
        const existing = await users.findByEmail(email);
        if (existing) return this.resolveExisting(existing);
        await users.add(user);
        await securityAudit.appendUserCreated({
          id: auditId,
          actorUserId: null,
          targetUserId: user.snapshot.id.value,
          nextRole: UserRole.SUPER_ADMIN,
          nextStatus: UserStatus.ACTIVE,
          correlationId: command.correlationId,
          createdAt: now,
        });
        return { userId: user.snapshot.id.value, email: email.value, created: true };
      });
    } catch (error) {
      if (!(error instanceof IamError) || error.code !== 'USER_EMAIL_ALREADY_USED') throw error;
      return this.unitOfWork.run(async ({ users }) => {
        const concurrent = await users.findByEmail(email);
        if (!concurrent) throw error;
        return this.resolveExisting(concurrent);
      });
    }
  }

  private resolveExisting(user: User): BootstrapSuperAdminResult {
    const state = user.snapshot;
    if (state.role !== UserRole.SUPER_ADMIN) {
      throw new BootstrapSuperAdminConflictError('现有账号不是 SUPER_ADMIN');
    }
    if (state.status !== UserStatus.ACTIVE) {
      throw new BootstrapSuperAdminConflictError('现有 SUPER_ADMIN 已被禁用');
    }
    return { userId: state.id.value, email: state.email.value, created: false };
  }
}
