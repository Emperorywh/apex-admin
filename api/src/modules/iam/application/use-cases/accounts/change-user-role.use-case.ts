import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { UserRole, UserStatus } from '../../../domain/account/user';
import { AuthenticatedActor } from '../../contracts/authenticated-actor';
import { toUserReadModel, UserReadModel } from '../../contracts/read-models';
import { IamErrors } from '../../errors/iam.error';
import { UserPolicy } from '../../policies/user.policy';
import { IamUnitOfWork } from '../../ports/persistence.ports';

/*
 * 角色修改在 Serializable 事务内重读目标、重跑 Policy 与最后超管不变量。
 * 同角色请求幂等返回当前投影，不产生虚假审计。
 */
@Injectable()
export class ChangeUserRoleUseCase {
  constructor(
    private readonly policy: UserPolicy,
    private readonly ids: IdGenerator,
    private readonly clock: Clock,
    private readonly unitOfWork: IamUnitOfWork,
  ) {}

  execute(command: {
    actor: AuthenticatedActor;
    targetUserId: string;
    nextRole: UserRole;
    correlationId: string;
  }): Promise<UserReadModel> {
    const now = this.clock.now();
    const auditId = this.ids.next();

    return this.unitOfWork.runSerializable(async ({ users, securityAudit }) => {
      const target = await users.lockById(command.targetUserId);
      if (!target) throw IamErrors.userNotFound();
      const before = target.snapshot;

      if (
        !this.policy.canAssignRole(
          command.actor,
          { id: before.id.value, role: before.role, status: before.status },
          command.nextRole,
        )
      ) {
        throw IamErrors.insufficientPrivilege();
      }

      if (before.role === command.nextRole) return toUserReadModel(target);
      if (
        before.role === UserRole.SUPER_ADMIN &&
        before.status === UserStatus.ACTIVE &&
        command.nextRole !== UserRole.SUPER_ADMIN &&
        (await users.countActiveSuperAdmins()) <= 1
      ) {
        throw IamErrors.lastSuperAdmin();
      }

      target.changeRole(command.nextRole, now);
      await users.save(target);
      await securityAudit.appendUserRoleChanged({
        id: auditId,
        actorUserId: command.actor.id,
        targetUserId: before.id.value,
        previousRole: before.role,
        nextRole: command.nextRole,
        correlationId: command.correlationId,
        createdAt: now,
      });
      return toUserReadModel(target);
    });
  }
}
