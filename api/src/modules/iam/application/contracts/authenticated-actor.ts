import { UserRole } from '../../domain/account/user';
import { PermissionCode } from '../../domain/authorization/authorization';

/*
 * AuthenticatedActor 是 access JWT 验证后的只读授权快照。
 * 它显式传入用例，禁止存入单例或隐藏请求上下文。
 */
export interface AuthenticatedActor {
  readonly id: string;
  readonly sessionId: string;
  readonly role: UserRole;
  readonly permissions: readonly PermissionCode[];
  readonly issuedAt: Date;
  readonly expiresAt: Date;
}
