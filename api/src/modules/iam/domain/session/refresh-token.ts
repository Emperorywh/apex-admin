import { DomainInvariantError } from '../errors/domain-invariant.error';
import { RefreshTokenId, SessionId } from '../account/identifiers';

/*
 * RefreshToken 领域对象只允许 ACTIVE 到 ROTATED 或 REVOKED。
 * 旧 token 的宽限与重放判定由持有 Session 上下文的应用用例完成。
 */
export enum RefreshTokenStatus {
  ACTIVE = 'ACTIVE',
  ROTATED = 'ROTATED',
  REVOKED = 'REVOKED',
}

export class TokenHash {
  private constructor(readonly value: string) {}

  static restore(value: string): TokenHash {
    if (!/^[0-9a-f]{64}$/.test(value)) {
      throw new DomainInvariantError('TOKEN_HASH_INVALID', 'TokenHash 必须是 SHA-256 小写十六进制');
    }
    return new TokenHash(value);
  }
}

export interface RefreshTokenState {
  readonly id: RefreshTokenId;
  readonly sessionId: SessionId;
  readonly tokenHash: TokenHash;
  readonly status: RefreshTokenStatus;
  readonly rotatedAt: Date | null;
  readonly revokedAt: Date | null;
  readonly createdAt: Date;
}

export class RefreshToken {
  private constructor(private state: RefreshTokenState) {}

  static create(input: {
    id: RefreshTokenId;
    sessionId: SessionId;
    tokenHash: TokenHash;
    now: Date;
  }): RefreshToken {
    return new RefreshToken({
      ...input,
      status: RefreshTokenStatus.ACTIVE,
      rotatedAt: null,
      revokedAt: null,
      createdAt: input.now,
    });
  }

  static restore(state: RefreshTokenState): RefreshToken {
    RefreshToken.assertValidState(state);
    return new RefreshToken(state);
  }

  get snapshot(): RefreshTokenState {
    return { ...this.state };
  }

  rotate(now: Date): void {
    this.assertActive();
    this.state = { ...this.state, status: RefreshTokenStatus.ROTATED, rotatedAt: now };
  }

  revoke(now: Date): void {
    this.assertActive();
    this.state = { ...this.state, status: RefreshTokenStatus.REVOKED, revokedAt: now };
  }

  private assertActive(): void {
    if (this.state.status !== RefreshTokenStatus.ACTIVE) {
      throw new DomainInvariantError('REFRESH_TOKEN_TRANSITION_INVALID', 'Refresh Token 状态迁移非法');
    }
  }

  private static assertValidState(state: RefreshTokenState): void {
    const activeIsValid =
      state.status === RefreshTokenStatus.ACTIVE &&
      state.rotatedAt === null &&
      state.revokedAt === null;
    const rotatedIsValid =
      state.status === RefreshTokenStatus.ROTATED &&
      state.rotatedAt !== null &&
      state.rotatedAt >= state.createdAt &&
      state.revokedAt === null;
    const revokedIsValid =
      state.status === RefreshTokenStatus.REVOKED &&
      state.rotatedAt === null &&
      state.revokedAt !== null &&
      state.revokedAt >= state.createdAt;
    if (!activeIsValid && !rotatedIsValid && !revokedIsValid) {
      throw new DomainInvariantError('REFRESH_TOKEN_STATE_INVALID', 'Refresh Token 状态组合非法');
    }
  }
}
