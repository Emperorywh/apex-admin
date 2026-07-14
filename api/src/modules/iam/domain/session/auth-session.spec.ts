import { SessionId, UserId } from '../account/identifiers';
import {
  AuthSession,
  AuthSessionStatus,
  SessionRevocationReason,
} from './auth-session';

/*
 * Session 测试覆盖三种撤销原因与重复撤销幂等语义。
 * 过期保持为时间推导，不写入额外枚举状态。
 */
describe('AuthSession', () => {
  const now = new Date('2026-07-14T00:00:00.000Z');

  const createSession = () =>
    AuthSession.create({
      id: SessionId.from('019f5fdf-100f-7c10-9748-6dc673e0b1fd'),
      userId: UserId.from('019f5fdf-100f-7c10-9748-6dc673e0b1fe'),
      now,
      expiresAt: new Date('2026-07-21T00:00:00.000Z'),
    });

  it.each(Object.values(SessionRevocationReason))('按 %s 显式撤销', (reason) => {
    const session = createSession();
    expect(session.revoke(reason, now)).toBe(true);
    expect(session.snapshot).toMatchObject({
      status: AuthSessionStatus.REVOKED,
      revocationReason: reason,
      revokedAt: now,
    });
    expect(session.revoke(reason, now)).toBe(false);
  });

  it('根据 expiresAt 推导过期', () => {
    expect(createSession().isUsable(new Date('2026-07-21T00:00:00.000Z'))).toBe(false);
  });

  it('恢复时拒绝状态与撤销元数据不一致', () => {
    const valid = createSession().snapshot;
    expect(() =>
      AuthSession.restore({
        ...valid,
        status: AuthSessionStatus.REVOKED,
      }),
    ).toThrow('状态组合非法');
  });
});
