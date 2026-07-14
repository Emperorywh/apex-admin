import { RefreshTokenId, SessionId, UserId } from '../../domain/account/identifiers';
import { UserStatus } from '../../domain/account/user';
import { AuthSession } from '../../domain/session/auth-session';
import {
  RefreshToken,
  RefreshTokenStatus,
  TokenHash,
} from '../../domain/session/refresh-token';
import { RefreshSessionPolicy } from './refresh-session.policy';

/*
 * Refresh Policy 测试穷尽有效、陈旧、重放、禁用与过期决策。
 * 测试不触碰仓储，确保状态判断可以独立推导。
 */
describe('RefreshSessionPolicy', () => {
  const policy = new RefreshSessionPolicy();
  const now = new Date('2026-07-14T00:00:10.000Z');

  it.each([
    [UserStatus.ACTIVE, RefreshTokenStatus.ACTIVE, null, 'ROTATE'],
    [UserStatus.ACTIVE, RefreshTokenStatus.ROTATED, new Date('2026-07-14T00:00:07Z'), 'STALE'],
    [UserStatus.ACTIVE, RefreshTokenStatus.ROTATED, new Date('2026-07-14T00:00:04Z'), 'REPLAY'],
    [UserStatus.ACTIVE, RefreshTokenStatus.REVOKED, null, 'INVALID'],
    [UserStatus.DISABLED, RefreshTokenStatus.ACTIVE, null, 'USER_DISABLED'],
  ] as const)('根据用户/token 状态返回 %s/%s → %s', (userStatus, status, endedAt, expected) => {
    expect(
      policy.decide({
        userStatus,
        session: createSession(new Date('2026-07-21T00:00:00Z')),
        token: createToken(status, endedAt),
        now,
        reuseGraceSeconds: 5,
      }),
    ).toBe(expected);
  });

  it('Session 绝对过期优先返回 INVALID', () => {
    expect(
      policy.decide({
        userStatus: UserStatus.ACTIVE,
        session: createSession(now),
        token: createToken(RefreshTokenStatus.ACTIVE, null),
        now,
        reuseGraceSeconds: 5,
      }),
    ).toBe('INVALID');
  });
});

function createSession(expiresAt: Date): AuthSession {
  return AuthSession.create({
    id: SessionId.from('019f5fdf-100f-7c10-9748-6dc673e0b110'),
    userId: UserId.from('019f5fdf-100f-7c10-9748-6dc673e0b111'),
    now: new Date('2026-07-01T00:00:00Z'),
    expiresAt,
  });
}

function createToken(status: RefreshTokenStatus, endedAt: Date | null): RefreshToken {
  return RefreshToken.restore({
    id: RefreshTokenId.from('019f5fdf-100f-7c10-9748-6dc673e0b112'),
    sessionId: SessionId.from('019f5fdf-100f-7c10-9748-6dc673e0b110'),
    tokenHash: TokenHash.restore('c'.repeat(64)),
    status,
    rotatedAt: status === RefreshTokenStatus.ROTATED ? endedAt : null,
    revokedAt: status === RefreshTokenStatus.REVOKED ? new Date('2026-07-14T00:00:05Z') : null,
    createdAt: new Date('2026-07-01T00:00:00Z'),
  });
}
