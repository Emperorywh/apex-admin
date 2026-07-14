import {
  type AuthSession as PrismaAuthSessionRecord,
  type RefreshToken as PrismaRefreshTokenRecord,
  type User as PrismaUserRecord,
} from '@prisma/client';
import { Email } from '../../../domain/account/email';
import { RefreshTokenId, SessionId, UserId } from '../../../domain/account/identifiers';
import { PasswordHash } from '../../../domain/account/password';
import { User, UserRole, UserStatus } from '../../../domain/account/user';
import {
  AuthSession,
  AuthSessionStatus,
  SessionRevocationReason,
} from '../../../domain/session/auth-session';
import {
  RefreshToken,
  RefreshTokenStatus,
  TokenHash,
} from '../../../domain/session/refresh-token';

/*
 * Prisma Mapper 集中隔离数据库记录、生成枚举与领域对象。
 * 任何持久化非法值都会在 restore 阶段失败关闭，而不是继续向上泄漏。
 */
export class PrismaIamMapper {
  static userToDomain(record: PrismaUserRecord): User {
    return User.restore({
      id: UserId.from(record.id),
      email: Email.restore(record.email),
      passwordHash: PasswordHash.restore(record.passwordHash),
      role: record.role as UserRole,
      status: record.status as UserStatus,
      createdAt: record.createdAt,
      updatedAt: record.updatedAt,
    });
  }

  static sessionToDomain(record: PrismaAuthSessionRecord): AuthSession {
    return AuthSession.restore({
      id: SessionId.from(record.id),
      userId: UserId.from(record.userId),
      status: record.status as AuthSessionStatus,
      expiresAt: record.expiresAt,
      revokedAt: record.revokedAt,
      revocationReason: record.revocationReason as SessionRevocationReason | null,
      createdAt: record.createdAt,
    });
  }

  static refreshTokenToDomain(record: PrismaRefreshTokenRecord): RefreshToken {
    return RefreshToken.restore({
      id: RefreshTokenId.from(record.id),
      sessionId: SessionId.from(record.sessionId),
      tokenHash: TokenHash.restore(record.tokenHash),
      status: record.status as RefreshTokenStatus,
      rotatedAt: record.rotatedAt,
      revokedAt: record.revokedAt,
      createdAt: record.createdAt,
    });
  }
}
