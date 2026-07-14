import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import {
  deriveUuidV8,
  IdGenerator,
} from '../../../../../shared/kernel/id-generator';
import { UserRole, UserStatus } from '../../../domain/account/user';
import { SessionRevocationReason } from '../../../domain/session/auth-session';
import { AuthenticatedActor } from '../../contracts/authenticated-actor';
import { IamErrors } from '../../errors/iam.error';
import { UserPolicy } from '../../policies/user.policy';
import { IamUnitOfWork } from '../../ports/persistence.ports';

/*
 * 禁用、全部活跃 Session 吊销与对应审计在同一 Serializable 事务提交。
 * 幂等禁用仍清理可能存在的活跃 Session，避免不一致状态长期残留。
 */
@Injectable()
export class DisableUserUseCase {
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
    const statusAuditId = this.ids.next();
    const sessionAuditNamespace = this.ids.next();

    await this.unitOfWork.runSerializable(
      async ({ users, sessions, securityAudit }) => {
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

        if (
          before.role === UserRole.SUPER_ADMIN &&
          before.status === UserStatus.ACTIVE &&
          (await users.countActiveSuperAdmins()) <= 1
        ) {
          throw IamErrors.lastSuperAdmin();
        }

        const changed = target.disable(now);
        if (changed) {
          await users.save(target);
          await securityAudit.appendUserStatusChanged({
            id: statusAuditId,
            actorUserId: command.actor.id,
            targetUserId: before.id.value,
            previousStatus: before.status,
            nextStatus: UserStatus.DISABLED,
            correlationId: command.correlationId,
            createdAt: now,
          });
        }

        const revokedSessionIds = await sessions.revokeAllActiveForUser(
          before.id.value,
          SessionRevocationReason.USER_DISABLED,
          now,
        );
        for (const sessionId of revokedSessionIds) {
          await securityAudit.appendSessionRevoked({
            id: deriveUuidV8(sessionAuditNamespace, sessionId),
            actorUserId: command.actor.id,
            targetUserId: before.id.value,
            sessionId,
            reason: SessionRevocationReason.USER_DISABLED,
            correlationId: command.correlationId,
            createdAt: now,
          });
        }
      },
    );
  }
}
