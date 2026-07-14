import { Prisma } from '@prisma/client';
import { AuthSessionRepository } from '../../../application/ports/persistence.ports';
import {
  AuthSession,
  SessionRevocationReason,
} from '../../../domain/session/auth-session';

/*
 * Session 仓储只持久化显式状态迁移，并按 ID 稳定顺序批量锁定。
 * 过期不会被写成伪状态，仍由 expiresAt 在用例中推导。
 */
type SessionExecutor = Pick<
  Prisma.TransactionClient,
  'authSession' | '$queryRaw'
>;

export class PrismaAuthSessionRepository extends AuthSessionRepository {
  constructor(private readonly database: SessionExecutor) {
    super();
  }

  async add(session: AuthSession): Promise<void> {
    const state = session.snapshot;
    await this.database.authSession.create({
      data: {
        id: state.id.value,
        userId: state.userId.value,
        status: state.status,
        expiresAt: state.expiresAt,
        revokedAt: state.revokedAt,
        revocationReason: state.revocationReason,
        createdAt: state.createdAt,
      },
    });
  }

  async save(session: AuthSession): Promise<void> {
    const state = session.snapshot;
    await this.database.authSession.update({
      where: { id: state.id.value },
      data: {
        status: state.status,
        revokedAt: state.revokedAt,
        revocationReason: state.revocationReason,
      },
    });
  }

  async revokeAllActiveForUser(
    userId: string,
    reason: SessionRevocationReason,
    now: Date,
  ): Promise<readonly string[]> {
    const rows = await this.database.$queryRaw<readonly { id: string }[]>(
      Prisma.sql`
        SELECT id
        FROM auth_sessions
        WHERE user_id = ${userId}::uuid AND status = 'ACTIVE'
        ORDER BY id
        FOR UPDATE
      `,
    );
    const ids = rows.map((row) => row.id);
    if (ids.length === 0) return ids;
    await this.database.authSession.updateMany({
      where: { id: { in: ids }, status: 'ACTIVE' },
      data: { status: 'REVOKED', revokedAt: now, revocationReason: reason },
    });
    return ids;
  }
}
