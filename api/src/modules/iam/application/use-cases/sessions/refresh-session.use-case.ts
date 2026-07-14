import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { RefreshTokenId } from '../../../domain/account/identifiers';
import { User } from '../../../domain/account/user';
import { ROLE_PERMISSIONS } from '../../../domain/authorization/authorization';
import {
  AuthSessionStatus,
  SessionRevocationReason,
} from '../../../domain/session/auth-session';
import { RefreshToken, TokenHash } from '../../../domain/session/refresh-token';
import { toUserReadModel, UserReadModel } from '../../contracts/read-models';
import { IamErrors } from '../../errors/iam.error';
import { RefreshSessionPolicy } from '../../policies/refresh-session.policy';
import { IamUnitOfWork } from '../../ports/persistence.ports';
import {
  AccessTokenService,
  IamSessionConfig,
  OpaqueTokenService,
} from '../../ports/security.ports';

/*
 * Refresh 事务只返回 Valid、Stale、Replay、Invalid 或 UserDisabled 封闭结果。
 * 重放吊销和审计先提交，随后才抛 HTTP 可映射的稳定应用错误。
 */
type RefreshTransactionResult =
  | {
      readonly kind: 'VALID';
      readonly user: User;
      readonly expiresAt: Date;
      readonly sessionId: string;
    }
  | { readonly kind: 'STALE' }
  | { readonly kind: 'REPLAY' }
  | { readonly kind: 'INVALID' }
  | { readonly kind: 'USER_DISABLED' };

@Injectable()
export class RefreshSessionUseCase {
  constructor(
    private readonly opaqueTokens: OpaqueTokenService,
    private readonly accessTokens: AccessTokenService,
    private readonly sessionConfig: IamSessionConfig,
    private readonly policy: RefreshSessionPolicy,
    private readonly ids: IdGenerator,
    private readonly clock: Clock,
    private readonly unitOfWork: IamUnitOfWork,
  ) {}

  async execute(command: {
    refreshToken: string | undefined;
    correlationId: string;
  }): Promise<{
    accessToken: string;
    expiresIn: number;
    refreshToken: string;
    refreshExpiresAt: Date;
    user: UserReadModel;
  }> {
    if (!command.refreshToken) throw IamErrors.refreshTokenInvalid();
    const now = this.clock.now();
    const currentHash = this.opaqueTokens.hash(command.refreshToken);
    const candidate = this.opaqueTokens.generate();
    const candidateId = RefreshTokenId.from(this.ids.next());
    const sessionRevokedAuditId = this.ids.next();
    const replayAuditId = this.ids.next();

    const transactionResult = await this.unitOfWork.run(
      async ({ sessions, refreshTokens, securityAudit }): Promise<RefreshTransactionResult> => {
        const context = await refreshTokens.lockContextByTokenHash(currentHash);
        if (!context) return { kind: 'INVALID' };
        const userState = context.user.snapshot;
        const sessionState = context.session.snapshot;
        const decision = this.policy.decide({
          userStatus: userState.status,
          session: context.session,
          token: context.token,
          now,
          reuseGraceSeconds: this.sessionConfig.refreshReuseGraceSeconds,
        });

        if (decision === 'USER_DISABLED') {
          if (sessionState.status === AuthSessionStatus.ACTIVE) {
            context.session.revoke(SessionRevocationReason.USER_DISABLED, now);
            await sessions.save(context.session);
            await securityAudit.appendSessionRevoked({
              id: sessionRevokedAuditId,
              actorUserId: null,
              targetUserId: userState.id.value,
              sessionId: sessionState.id.value,
              reason: SessionRevocationReason.USER_DISABLED,
              correlationId: command.correlationId,
              createdAt: now,
            });
          }
          return { kind: 'USER_DISABLED' };
        }

        if (decision === 'INVALID') return { kind: 'INVALID' };
        if (decision === 'STALE') return { kind: 'STALE' };
        if (decision === 'REPLAY') {
          context.session.revoke(SessionRevocationReason.REFRESH_TOKEN_REPLAY, now);
          await sessions.save(context.session);
          await securityAudit.appendSessionRevoked({
            id: sessionRevokedAuditId,
            actorUserId: null,
            targetUserId: userState.id.value,
            sessionId: sessionState.id.value,
            reason: SessionRevocationReason.REFRESH_TOKEN_REPLAY,
            correlationId: command.correlationId,
            createdAt: now,
          });
          await securityAudit.appendRefreshReplayDetected({
            id: replayAuditId,
            targetUserId: userState.id.value,
            sessionId: sessionState.id.value,
            correlationId: command.correlationId,
            createdAt: now,
          });
          return { kind: 'REPLAY' };
        }

        context.token.rotate(now);
        await refreshTokens.save(context.token);
        await refreshTokens.add(
          RefreshToken.create({
            id: candidateId,
            sessionId: sessionState.id,
            tokenHash: TokenHash.restore(candidate.hash),
            now,
          }),
        );
        return {
          kind: 'VALID',
          user: context.user,
          expiresAt: sessionState.expiresAt,
          sessionId: sessionState.id.value,
        };
      },
    );

    if (transactionResult.kind === 'STALE') throw IamErrors.refreshTokenStale();
    if (transactionResult.kind === 'REPLAY') throw IamErrors.refreshTokenReplay();
    if (transactionResult.kind === 'USER_DISABLED') throw IamErrors.userDisabled();
    if (transactionResult.kind === 'INVALID') throw IamErrors.refreshTokenInvalid();

    const userState = transactionResult.user.snapshot;
    const accessToken = this.accessTokens.sign({
      userId: userState.id.value,
      sessionId: transactionResult.sessionId,
      role: userState.role,
      permissions: ROLE_PERMISSIONS[userState.role],
      now,
    });
    return {
      accessToken: accessToken.value,
      expiresIn: accessToken.expiresInSeconds,
      refreshToken: candidate.value,
      refreshExpiresAt: transactionResult.expiresAt,
      user: toUserReadModel(transactionResult.user),
    };
  }
}
