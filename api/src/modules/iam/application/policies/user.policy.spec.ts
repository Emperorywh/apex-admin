import { UserRole, UserStatus } from '../../domain/account/user';
import { ROLE_RANK, ROLE_PERMISSIONS } from '../../domain/authorization/authorization';
import { AuthenticatedActor } from '../contracts/authenticated-actor';
import { UserPolicy } from './user.policy';

/*
 * UserPolicy 使用四角色全矩阵验证创建、赋权、启停与自操作规则。
 * 路由权限与最后超管不变量不混入本策略测试。
 */
describe('UserPolicy', () => {
  const policy = new UserPolicy();
  const actor = (role: UserRole, id = `actor-${role}`): AuthenticatedActor => ({
    id,
    sessionId: 'session',
    role,
    permissions: ROLE_PERMISSIONS[role],
    issuedAt: new Date(0),
    expiresAt: new Date(1),
  });

  for (const actorRole of Object.values(UserRole)) {
    for (const requestedRole of Object.values(UserRole)) {
      it(`${actorRole} 创建 ${requestedRole}`, () => {
        const expected =
          actorRole === UserRole.SUPER_ADMIN ||
          ROLE_RANK[requestedRole] < ROLE_RANK[actorRole];
        expect(policy.canCreate(actor(actorRole), requestedRole)).toBe(expected);
      });
    }

    for (const targetRole of Object.values(UserRole)) {
      for (const nextRole of Object.values(UserRole)) {
        it(`${actorRole} 将 ${targetRole} 赋为 ${nextRole}`, () => {
          const target = { id: 'target', role: targetRole, status: UserStatus.ACTIVE };
          const expected =
            actorRole === UserRole.SUPER_ADMIN ||
            (ROLE_RANK[targetRole] < ROLE_RANK[actorRole] &&
              ROLE_RANK[nextRole] < ROLE_RANK[actorRole]);
          expect(policy.canAssignRole(actor(actorRole), target, nextRole)).toBe(expected);
        });
      }

      it(`${actorRole} 启停 ${targetRole}`, () => {
        const target = { id: 'target', role: targetRole, status: UserStatus.ACTIVE };
        const expected =
          actorRole === UserRole.SUPER_ADMIN || ROLE_RANK[targetRole] < ROLE_RANK[actorRole];
        expect(policy.canChangeStatus(actor(actorRole), target)).toBe(expected);
      });
    }

    it(`${actorRole} 不能修改自己`, () => {
      const current = actor(actorRole, 'same-id');
      const target = { id: 'same-id', role: actorRole, status: UserStatus.ACTIVE };
      expect(policy.canAssignRole(current, target, UserRole.VIEWER)).toBe(false);
      expect(policy.canChangeStatus(current, target)).toBe(false);
    });
  }
});
