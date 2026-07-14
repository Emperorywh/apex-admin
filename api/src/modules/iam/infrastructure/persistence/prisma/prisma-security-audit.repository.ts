import { Prisma } from '@prisma/client';
import {
  SecurityAuditRepository,
  UserCreatedAudit,
} from '../../../application/ports/persistence.ports';

/*
 * 安全审计仓储只使用参数化 INSERT，不要求 Runtime Role 拥有 SELECT/UPDATE/DELETE。
 * 每个公开方法对应一个固定 payload 形状，不提供 action+any 的魔法入口。
 */
type AuditExecutor = Pick<Prisma.TransactionClient, '$executeRaw'>;

export class PrismaSecurityAuditRepository extends SecurityAuditRepository {
  constructor(private readonly database: AuditExecutor) {
    super();
  }

  async appendUserCreated(event: UserCreatedAudit): Promise<void> {
    await this.database.$executeRaw(
      Prisma.sql`
        INSERT INTO security_audit_events (
          id, action, actor_user_id, target_user_id, next_role, next_status,
          correlation_id, created_at
        ) VALUES (
          ${event.id}::uuid,
          'USER_CREATED'::security_audit_action,
          ${event.actorUserId}::uuid,
          ${event.targetUserId}::uuid,
          ${event.nextRole}::user_role,
          ${event.nextStatus}::user_status,
          ${event.correlationId},
          ${event.createdAt}
        )
      `,
    );
  }

  async appendUserRoleChanged(event: {
    id: string;
    actorUserId: string;
    targetUserId: string;
    previousRole: import('../../../domain/account/user').UserRole;
    nextRole: import('../../../domain/account/user').UserRole;
    correlationId: string;
    createdAt: Date;
  }): Promise<void> {
    await this.database.$executeRaw(
      Prisma.sql`
        INSERT INTO security_audit_events (
          id, action, actor_user_id, target_user_id, previous_role, next_role,
          correlation_id, created_at
        ) VALUES (
          ${event.id}::uuid,
          'USER_ROLE_CHANGED'::security_audit_action,
          ${event.actorUserId}::uuid,
          ${event.targetUserId}::uuid,
          ${event.previousRole}::user_role,
          ${event.nextRole}::user_role,
          ${event.correlationId},
          ${event.createdAt}
        )
      `,
    );
  }

  async appendUserStatusChanged(event: {
    id: string;
    actorUserId: string;
    targetUserId: string;
    previousStatus: import('../../../domain/account/user').UserStatus;
    nextStatus: import('../../../domain/account/user').UserStatus;
    correlationId: string;
    createdAt: Date;
  }): Promise<void> {
    await this.database.$executeRaw(
      Prisma.sql`
        INSERT INTO security_audit_events (
          id, action, actor_user_id, target_user_id, previous_status, next_status,
          correlation_id, created_at
        ) VALUES (
          ${event.id}::uuid,
          'USER_STATUS_CHANGED'::security_audit_action,
          ${event.actorUserId}::uuid,
          ${event.targetUserId}::uuid,
          ${event.previousStatus}::user_status,
          ${event.nextStatus}::user_status,
          ${event.correlationId},
          ${event.createdAt}
        )
      `,
    );
  }

  async appendSessionCreated(event: {
    id: string;
    actorUserId: string;
    targetUserId: string;
    sessionId: string;
    correlationId: string;
    createdAt: Date;
  }): Promise<void> {
    await this.database.$executeRaw(
      Prisma.sql`
        INSERT INTO security_audit_events (
          id, action, actor_user_id, target_user_id, session_id,
          correlation_id, created_at
        ) VALUES (
          ${event.id}::uuid,
          'SESSION_CREATED'::security_audit_action,
          ${event.actorUserId}::uuid,
          ${event.targetUserId}::uuid,
          ${event.sessionId}::uuid,
          ${event.correlationId},
          ${event.createdAt}
        )
      `,
    );
  }

  async appendSessionRevoked(event: {
    id: string;
    actorUserId: string | null;
    targetUserId: string;
    sessionId: string;
    reason: import('../../../domain/session/auth-session').SessionRevocationReason;
    correlationId: string;
    createdAt: Date;
  }): Promise<void> {
    await this.database.$executeRaw(
      Prisma.sql`
        INSERT INTO security_audit_events (
          id, action, actor_user_id, target_user_id, session_id,
          revocation_reason, correlation_id, created_at
        ) VALUES (
          ${event.id}::uuid,
          'SESSION_REVOKED'::security_audit_action,
          ${event.actorUserId}::uuid,
          ${event.targetUserId}::uuid,
          ${event.sessionId}::uuid,
          ${event.reason}::session_revocation_reason,
          ${event.correlationId},
          ${event.createdAt}
        )
      `,
    );
  }

  async appendRefreshReplayDetected(event: {
    id: string;
    targetUserId: string;
    sessionId: string;
    correlationId: string;
    createdAt: Date;
  }): Promise<void> {
    await this.database.$executeRaw(
      Prisma.sql`
        INSERT INTO security_audit_events (
          id, action, target_user_id, session_id, revocation_reason,
          correlation_id, created_at
        ) VALUES (
          ${event.id}::uuid,
          'REFRESH_REPLAY_DETECTED'::security_audit_action,
          ${event.targetUserId}::uuid,
          ${event.sessionId}::uuid,
          'REFRESH_TOKEN_REPLAY'::session_revocation_reason,
          ${event.correlationId},
          ${event.createdAt}
        )
      `,
    );
  }
}
