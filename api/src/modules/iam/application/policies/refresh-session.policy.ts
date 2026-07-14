import { UserStatus } from '../../domain/account/user';
import { AuthSession } from '../../domain/session/auth-session';
import { RefreshToken, RefreshTokenStatus } from '../../domain/session/refresh-token';

/*
 * Refresh Policy 纯粹把持久化快照、当前时间和宽限阈值归约为封闭决策。
 * 数据库写入与审计仍由 Use Case 编排，避免策略跨层访问仓储。
 */
export type RefreshDecision =
  | 'ROTATE'
  | 'STALE'
  | 'REPLAY'
  | 'INVALID'
  | 'USER_DISABLED';

export class RefreshSessionPolicy {
  decide(input: {
    userStatus: UserStatus;
    session: AuthSession;
    token: RefreshToken;
    now: Date;
    reuseGraceSeconds: number;
  }): RefreshDecision {
    if (input.userStatus === UserStatus.DISABLED) return 'USER_DISABLED';
    if (!input.session.isUsable(input.now)) return 'INVALID';

    const token = input.token.snapshot;
    if (token.status === RefreshTokenStatus.REVOKED) return 'INVALID';
    if (token.status === RefreshTokenStatus.ACTIVE) return 'ROTATE';
    if (!token.rotatedAt) return 'INVALID';

    const ageSeconds = (input.now.getTime() - token.rotatedAt.getTime()) / 1000;
    return ageSeconds <= input.reuseGraceSeconds ? 'STALE' : 'REPLAY';
  }
}
