import { PasswordHash } from '../../domain/account/password';
import { AuthenticatedActor } from '../contracts/authenticated-actor';
import { UserRole } from '../../domain/account/user';
import { PermissionCode } from '../../domain/authorization/authorization';

/*
 * 安全端口隔离高成本哈希、随机 token、JWT 与限流实现。
 * Application 只编排顺序与结果，不依赖具体密码库或令牌库。
 */
export abstract class PasswordHasher {
  abstract hash(password: string): Promise<PasswordHash>;
  abstract verify(passwordHash: PasswordHash, password: string): Promise<boolean>;
}

/*
 * 密码哈希阈值是 Runtime 与 Seed 共用的窄技术契约。
 * 两个进程各自校验环境后提供实现，不共享无关必填变量。
 */
export abstract class PasswordHashingConfig {
  abstract readonly memoryKiB: number;
  abstract readonly timeCost: number;
  abstract readonly parallelism: number;
  abstract readonly maxConcurrency: number;
}

export abstract class PasswordBlocklist {
  abstract contains(password: string, email: string): boolean;
}

export interface OpaqueToken {
  readonly value: string;
  readonly hash: string;
}

export abstract class OpaqueTokenService {
  abstract generate(): OpaqueToken;
  abstract hash(value: string): string;
}

export interface SignAccessTokenInput {
  readonly userId: string;
  readonly sessionId: string;
  readonly role: UserRole;
  readonly permissions: readonly PermissionCode[];
  readonly now: Date;
}

export interface SignedAccessToken {
  readonly value: string;
  readonly expiresAt: Date;
  readonly expiresInSeconds: number;
}

export abstract class AccessTokenService {
  abstract sign(input: SignAccessTokenInput): SignedAccessToken;
  abstract verify(token: string): AuthenticatedActor;
}

export abstract class LoginRateLimiter {
  abstract consume(ipAddress: string, canonicalEmail: string, now: Date): void;
}

/*
 * 会话阈值通过窄配置端口进入 Application。
 * 这避免用例依赖 RuntimeConfig 的 HTTP、数据库等无关配置结构。
 */
export abstract class IamSessionConfig {
  abstract readonly sessionTtlSeconds: number;
  abstract readonly refreshReuseGraceSeconds: number;
}
