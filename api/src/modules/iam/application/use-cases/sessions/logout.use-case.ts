import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { SessionRevocationReason } from '../../../domain/session/auth-session';
import { RefreshTokenStatus } from '../../../domain/session/refresh-token';
import { IamUnitOfWork } from '../../ports/persistence.ports';
import { OpaqueTokenService } from '../../ports/security.ports';

/*
 * Logout 使用任一已知 token 定位并撤销整个 Session，且不执行重放判定。
 * 缺失、未知或已经撤销的 token 都幂等成功，不泄露历史有效性。
 */
@Injectable()
export class LogoutUseCase {
  constructor(
    private readonly opaqueTokens: OpaqueTokenService,
    private readonly ids: IdGenerator,
    private readonly clock: Clock,
    private readonly unitOfWork: IamUnitOfWork,
  ) {}

  async execute(command: {
    refreshToken: string | undefined;
    correlationId: string;
  }): Promise<void> {
    if (!command.refreshToken) return;
    const tokenHash = this.opaqueTokens.hash(command.refreshToken);
    const now = this.clock.now();
    const auditId = this.ids.next();

    await this.unitOfWork.run(
      async ({ sessions, refreshTokens, securityAudit }) => {
        const context = await refreshTokens.lockContextByTokenHash(tokenHash);
        if (!context) return;
        const sessionState = context.session.snapshot;
        if (!context.session.revoke(SessionRevocationReason.LOGOUT, now)) return;

        await sessions.save(context.session);
        if (context.token.snapshot.status === RefreshTokenStatus.ACTIVE) {
          context.token.revoke(now);
          await refreshTokens.save(context.token);
        }
        await securityAudit.appendSessionRevoked({
          id: auditId,
          actorUserId: null,
          targetUserId: context.user.snapshot.id.value,
          sessionId: sessionState.id.value,
          reason: SessionRevocationReason.LOGOUT,
          correlationId: command.correlationId,
          createdAt: now,
        });
      },
    );
  }
}
