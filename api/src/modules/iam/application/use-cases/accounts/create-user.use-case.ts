import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { Email } from '../../../domain/account/email';
import { UserId } from '../../../domain/account/identifiers';
import { NewPassword } from '../../../domain/account/password';
import { User, UserRole, UserStatus } from '../../../domain/account/user';
import { DomainInvariantError } from '../../../domain/errors/domain-invariant.error';
import { AuthenticatedActor } from '../../contracts/authenticated-actor';
import { toUserReadModel, UserReadModel } from '../../contracts/read-models';
import { IamErrors } from '../../errors/iam.error';
import { UserPolicy } from '../../policies/user.policy';
import { IamUnitOfWork } from '../../ports/persistence.ports';
import { PasswordBlocklist, PasswordHasher } from '../../ports/security.ports';

/*
 * 创建用户严格按授权、密码策略/哈希、短事务的顺序执行。
 * 友好查重与数据库唯一约束共同保证 canonical email 唯一。
 */
@Injectable()
export class CreateUserUseCase {
  constructor(
    private readonly policy: UserPolicy,
    private readonly blocklist: PasswordBlocklist,
    private readonly passwordHasher: PasswordHasher,
    private readonly ids: IdGenerator,
    private readonly clock: Clock,
    private readonly unitOfWork: IamUnitOfWork,
  ) {}

  async execute(command: {
    actor: AuthenticatedActor;
    email: string;
    password: string;
    role: UserRole;
    correlationId: string;
  }): Promise<UserReadModel> {
    if (!this.policy.canCreate(command.actor, command.role)) {
      throw IamErrors.insufficientPrivilege();
    }

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
      role: command.role,
      now,
    });
    const auditId = this.ids.next();

    await this.unitOfWork.run(async ({ users, securityAudit }) => {
      if (await users.findByEmail(email)) throw IamErrors.userEmailAlreadyUsed();
      await users.add(user);
      await securityAudit.appendUserCreated({
        id: auditId,
        actorUserId: command.actor.id,
        targetUserId: user.snapshot.id.value,
        nextRole: command.role,
        nextStatus: UserStatus.ACTIVE,
        correlationId: command.correlationId,
        createdAt: now,
      });
    });

    return toUserReadModel(user);
  }
}
