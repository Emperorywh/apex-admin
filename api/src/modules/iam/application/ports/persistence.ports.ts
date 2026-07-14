import { User, UserRole, UserStatus } from '../../domain/account/user';
import { Email } from '../../domain/account/email';
import { AuthSession, SessionRevocationReason } from '../../domain/session/auth-session';
import { RefreshToken } from '../../domain/session/refresh-token';
import {
  UserAuthorizationSnapshot,
  UserPage,
  UserPageCursor,
  UserReadModel,
} from '../contracts/read-models';

/*
 * 仓储端口只表达 IAM 用例真实需要的持久化能力。
 * TransactionClient、WhereInput 与数据库枚举不会泄漏到应用层。
 */
export interface CredentialSnapshot {
  readonly user: User;
}

export interface RefreshContext {
  readonly user: User;
  readonly session: AuthSession;
  readonly token: RefreshToken;
}

export abstract class UserRepository {
  abstract findByEmail(email: Email): Promise<User | null>;
  abstract findCredentialByEmail(email: Email): Promise<CredentialSnapshot | null>;
  abstract findById(id: string): Promise<User | null>;
  abstract findReadModelById(id: string): Promise<UserReadModel | null>;
  abstract findAuthorizationSnapshotById(
    id: string,
  ): Promise<UserAuthorizationSnapshot | null>;
  abstract lockById(id: string): Promise<User | null>;
  abstract add(user: User): Promise<void>;
  abstract save(user: User): Promise<void>;
  abstract countActiveSuperAdmins(): Promise<number>;
  abstract list(input: {
    pageSize: number;
    cursor: UserPageCursor | null;
  }): Promise<UserPage>;
}

export abstract class AuthSessionRepository {
  abstract add(session: AuthSession): Promise<void>;
  abstract save(session: AuthSession): Promise<void>;
  abstract revokeAllActiveForUser(
    userId: string,
    reason: SessionRevocationReason,
    now: Date,
  ): Promise<readonly string[]>;
}

export abstract class RefreshTokenRepository {
  abstract add(token: RefreshToken): Promise<void>;
  abstract save(token: RefreshToken): Promise<void>;
  abstract lockContextByTokenHash(tokenHash: string): Promise<RefreshContext | null>;
}

export interface UserCreatedAudit {
  readonly id: string;
  readonly actorUserId: string | null;
  readonly targetUserId: string;
  readonly nextRole: UserRole;
  readonly nextStatus: UserStatus;
  readonly correlationId: string;
  readonly createdAt: Date;
}

export abstract class SecurityAuditRepository {
  abstract appendUserCreated(event: UserCreatedAudit): Promise<void>;
  abstract appendUserRoleChanged(event: {
    id: string;
    actorUserId: string;
    targetUserId: string;
    previousRole: UserRole;
    nextRole: UserRole;
    correlationId: string;
    createdAt: Date;
  }): Promise<void>;
  abstract appendUserStatusChanged(event: {
    id: string;
    actorUserId: string;
    targetUserId: string;
    previousStatus: UserStatus;
    nextStatus: UserStatus;
    correlationId: string;
    createdAt: Date;
  }): Promise<void>;
  abstract appendSessionCreated(event: {
    id: string;
    actorUserId: string;
    targetUserId: string;
    sessionId: string;
    correlationId: string;
    createdAt: Date;
  }): Promise<void>;
  abstract appendSessionRevoked(event: {
    id: string;
    actorUserId: string | null;
    targetUserId: string;
    sessionId: string;
    reason: SessionRevocationReason;
    correlationId: string;
    createdAt: Date;
  }): Promise<void>;
  abstract appendRefreshReplayDetected(event: {
    id: string;
    targetUserId: string;
    sessionId: string;
    correlationId: string;
    createdAt: Date;
  }): Promise<void>;
}

export interface IamTransaction {
  readonly users: UserRepository;
  readonly sessions: AuthSessionRepository;
  readonly refreshTokens: RefreshTokenRepository;
  readonly securityAudit: SecurityAuditRepository;
}

export abstract class IamUnitOfWork {
  abstract run<T>(work: (transaction: IamTransaction) => Promise<T>): Promise<T>;
  abstract runSerializable<T>(
    work: (transaction: IamTransaction) => Promise<T>,
  ): Promise<T>;
}
