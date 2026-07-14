import { RefreshTokenId, SessionId } from '../account/identifiers';
import { RefreshToken, RefreshTokenStatus, TokenHash } from './refresh-token';

/*
 * RefreshToken 测试固定 ACTIVE 的两个合法终点与非法二次迁移。
 * 宽限/重放时间判断留在应用用例测试中。
 */
describe('RefreshToken', () => {
  const now = new Date('2026-07-14T00:00:00.000Z');
  const createToken = () =>
    RefreshToken.create({
      id: RefreshTokenId.from('019f5fdf-100f-7c10-9748-6dc673e0b1fa'),
      sessionId: SessionId.from('019f5fdf-100f-7c10-9748-6dc673e0b1fb'),
      tokenHash: TokenHash.restore('a'.repeat(64)),
      now,
    });

  it('允许 ACTIVE 到 ROTATED', () => {
    const token = createToken();
    token.rotate(now);
    expect(token.snapshot.status).toBe(RefreshTokenStatus.ROTATED);
    expect(() => token.revoke(now)).toThrow('状态迁移非法');
  });

  it('允许 ACTIVE 到 REVOKED', () => {
    const token = createToken();
    token.revoke(now);
    expect(token.snapshot.status).toBe(RefreshTokenStatus.REVOKED);
    expect(() => token.rotate(now)).toThrow('状态迁移非法');
  });

  it('恢复时拒绝非法可空字段组合', () => {
    const state = createToken().snapshot;
    expect(() =>
      RefreshToken.restore({
        ...state,
        status: RefreshTokenStatus.ROTATED,
      }),
    ).toThrow('状态组合非法');
  });
});
