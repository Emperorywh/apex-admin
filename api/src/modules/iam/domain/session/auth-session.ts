import { SessionId, UserId } from '../account/identifiers';
import { DomainInvariantError } from '../errors/domain-invariant.error';

/*
 * Session 过期是时间推导状态；主动失效才持久化 REVOKED。
 * revoke 返回是否发生迁移，使幂等用例不会重复写审计。
 */
export enum AuthSessionStatus {
  ACTIVE = 'ACTIVE',
  REVOKED = 'REVOKED',
}

export enum SessionRevocationReason {
  LOGOUT = 'LOGOUT',
  REFRESH_TOKEN_REPLAY = 'REFRESH_TOKEN_REPLAY',
  USER_DISABLED = 'USER_DISABLED',
}

export interface AuthSessionState {
  readonly id: SessionId;
  readonly userId: UserId;
  readonly status: AuthSessionStatus;
  readonly expiresAt: Date;
  readonly revokedAt: Date | null;
  readonly revocationReason: SessionRevocationReason | null;
  readonly createdAt: Date;
}

export class AuthSession {
  private constructor(private state: AuthSessionState) {}

  static create(input: {
    id: SessionId;
    userId: UserId;
    now: Date;
    expiresAt: Date;
  }): AuthSession {
    const state: AuthSessionState = {
      id: input.id,
      userId: input.userId,
      status: AuthSessionStatus.ACTIVE,
      expiresAt: input.expiresAt,
      revokedAt: null,
      revocationReason: null,
      createdAt: input.now,
    };
    AuthSession.assertValidState(state);
    return new AuthSession(state);
  }

  static restore(state: AuthSessionState): AuthSession {
    AuthSession.assertValidState(state);
    return new AuthSession(state);
  }

  get snapshot(): AuthSessionState {
    return { ...this.state };
  }

  isUsable(now: Date): boolean {
    return this.state.status === AuthSessionStatus.ACTIVE && this.state.expiresAt > now;
  }

  revoke(reason: SessionRevocationReason, now: Date): boolean {
    if (this.state.status === AuthSessionStatus.REVOKED) return false;
    this.state = {
      ...this.state,
      status: AuthSessionStatus.REVOKED,
      revokedAt: now,
      revocationReason: reason,
    };
    return true;
  }

  private static assertValidState(state: AuthSessionState): void {
    const activeIsValid =
      state.status === AuthSessionStatus.ACTIVE &&
      state.revokedAt === null &&
      state.revocationReason === null;
    const revokedIsValid =
      state.status === AuthSessionStatus.REVOKED &&
      state.revokedAt !== null &&
      state.revokedAt >= state.createdAt &&
      state.revocationReason !== null;
    if (state.expiresAt <= state.createdAt || (!activeIsValid && !revokedIsValid)) {
      throw new DomainInvariantError('AUTH_SESSION_STATE_INVALID', 'Session 状态组合非法');
    }
  }
}
