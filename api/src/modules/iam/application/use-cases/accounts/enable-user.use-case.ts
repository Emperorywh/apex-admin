import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { UserStatus } from '../../../domain/account/user';
import { AuthenticatedActor } from '../../contracts/authenticated-actor';
import { IamErrors } from '../../errors/iam.error';
import { UserPolicy } from '../../policies/user.policy';
import { IamUnitOfWork } from '../../ports/persistence.ports';

/*
 * 启用使用普通短事务和 User 行锁，并在锁后重新执行对象级 Policy。
 * 已启用用户幂等成功，历史 Session 不会被恢复。
 */
@Injectable()
export class EnableUserUseCase {
  constructor(
    private readonly policy: UserPolicy,
    private readonly ids: IdGenerator,
    private readonly clock: Clock,
    private readonly unitOfWork: IamUnitOfWork,
  ) {}

  async execute(command: {
    actor: AuthenticatedActor;
    targetUserId: string;
    correlationId: string;
  }): Promise<void> {
    const now = this.clock.now();
    const auditId = this.ids.next();
    await this.unitOfWork.run(async ({ users, securityAudit }) => {
      const target = await users.lockById(command.targetUserId);
      if (!target) throw IamErrors.userNotFound();
      const before = target.snapshot;
      if (
        !this.policy.canChangeStatus(command.actor, {
          id: before.id.value,
          role: before.role,
          status: before.status,
        })
      ) {
        throw IamErrors.insufficientPrivilege();
      }
      if (!target.enable(now)) return;
      await users.save(target);
      await securityAudit.appendUserStatusChanged({
        id: auditId,
        actorUserId: command.actor.id,
        targetUserId: before.id.value,
        previousStatus: before.status,
        nextStatus: UserStatus.ACTIVE,
        correlationId: command.correlationId,
        createdAt: now,
      });
    });
  }
}
