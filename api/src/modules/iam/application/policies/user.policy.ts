import { AuthenticatedActor } from '../contracts/authenticated-actor';
import { UserAuthorizationSnapshot } from '../contracts/read-models';
import { UserRole } from '../../domain/account/user';
import { ROLE_RANK } from '../../domain/authorization/authorization';

/*
 * UserPolicy 只判断 actor、目标与期望状态之间的对象级授权关系。
 * 路由能力与最后超管计数分别由 Guard 和事务用例负责。
 */
export class UserPolicy {
  canCreate(actor: AuthenticatedActor, requestedRole: UserRole): boolean {
    if (actor.role === UserRole.SUPER_ADMIN) return true;
    return ROLE_RANK[requestedRole] < ROLE_RANK[actor.role];
  }

  canAssignRole(
    actor: AuthenticatedActor,
    target: UserAuthorizationSnapshot,
    nextRole: UserRole,
  ): boolean {
    if (actor.id === target.id) return false;
    if (actor.role === UserRole.SUPER_ADMIN) return true;
    return (
      ROLE_RANK[target.role] < ROLE_RANK[actor.role] &&
      ROLE_RANK[nextRole] < ROLE_RANK[actor.role]
    );
  }

  canChangeStatus(
    actor: AuthenticatedActor,
    target: UserAuthorizationSnapshot,
  ): boolean {
    if (actor.id === target.id) return false;
    if (actor.role === UserRole.SUPER_ADMIN) return true;
    return ROLE_RANK[target.role] < ROLE_RANK[actor.role];
  }
}
