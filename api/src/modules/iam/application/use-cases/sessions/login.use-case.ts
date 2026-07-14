import { Injectable } from '@nestjs/common';
import { Clock } from '../../../../../shared/kernel/clock';
import { IdGenerator } from '../../../../../shared/kernel/id-generator';
import { Email } from '../../../domain/account/email';
import { RefreshTokenId, SessionId, UserId } from '../../../domain/account/identifiers';
import { normalizePassword } from '../../../domain/account/password';
import { UserStatus } from '../../../domain/account/user';
import { ROLE_PERMISSIONS } from '../../../domain/authorization/authorization';
import { AuthSession } from '../../../domain/session/auth-session';
import { RefreshToken, TokenHash } from '../../../domain/session/refresh-token';
import { toUserReadModel, UserReadModel } from '../../contracts/read-models';
import { IamErrors } from '../../errors/iam.error';
import { IamUnitOfWork, UserRepository } from '../../ports/persistence.ports';
import {
  AccessTokenService,
  IamSessionConfig,
  LoginRateLimiter,
  OpaqueTokenService,
  PasswordHasher,
} from '../../ports/security.ports';

/*
 * 登录先限流与事务外 Argon2 验证，再锁定 User 重验 ACTIVE 并原子创建会话。
 * JWT 只在事务提交后签发，避免把密码计算或令牌库放进数据库事务。
 */
@Injectable()
export class LoginUseCase {
  constructor(
    private readonly users: UserRepository,
    private readonly limiter: LoginRateLimiter,
    private readonly passwordHasher: PasswordHasher,
    private readonly opaqueTokens: OpaqueTokenService,
    private readonly accessTokens: AccessTokenService,
    private readonly sessionConfig: IamSessionConfig,
    private readonly ids: IdGenerator,
    private readonly clock: Clock,
    private readonly unitOfWork: IamUnitOfWork,
  ) {}

  async execute(command: {
    email: string;
    password: string;
    ipAddress: string;
    correlationId: string;
  }): Promise<{
    accessToken: string;
    expiresIn: number;
    refreshToken: string;
    refreshExpiresAt: Date;
    user: UserReadModel;
  }> {
    const email = Email.create(command.email);
    const now = this.clock.now();
    this.limiter.consume(command.ipAddress, email.value, now);

    const credential = await this.users.findCredentialByEmail(email);
    if (!credential) throw IamErrors.loginAccountNotFound();
    const credentialState = credential.user.snapshot;
    const matches = await this.passwordHasher.verify(
      credentialState.passwordHash,
      normalizePassword(command.password),
    );
    if (!matches) throw IamErrors.invalidCredentials();
    if (credentialState.status === UserStatus.DISABLED) throw IamErrors.userDisabled();

    const sessionId = SessionId.from(this.ids.next());
    const refreshTokenId = RefreshTokenId.from(this.ids.next());
    const auditId = this.ids.next();
    const opaqueToken = this.opaqueTokens.generate();
    const expiresAt = new Date(now.getTime() + this.sessionConfig.sessionTtlSeconds * 1000);
    const session = AuthSession.create({
      id: sessionId,
      userId: UserId.from(credentialState.id.value),
      now,
      expiresAt,
    });
    const refreshToken = RefreshToken.create({
      id: refreshTokenId,
      sessionId,
      tokenHash: TokenHash.restore(opaqueToken.hash),
      now,
    });

    const currentUser = await this.unitOfWork.run(
      async ({ users, sessions, refreshTokens, securityAudit }) => {
        const lockedUser = await users.lockById(credentialState.id.value);
        if (!lockedUser || lockedUser.snapshot.status === UserStatus.DISABLED) {
          throw IamErrors.userDisabled();
        }
        await sessions.add(session);
        await refreshTokens.add(refreshToken);
        await securityAudit.appendSessionCreated({
          id: auditId,
          actorUserId: lockedUser.snapshot.id.value,
          targetUserId: lockedUser.snapshot.id.value,
          sessionId: sessionId.value,
          correlationId: command.correlationId,
          createdAt: now,
        });
        return lockedUser;
      },
    );

    const currentState = currentUser.snapshot;
    const accessToken = this.accessTokens.sign({
      userId: currentState.id.value,
      sessionId: sessionId.value,
      role: currentState.role,
      permissions: ROLE_PERMISSIONS[currentState.role],
      now,
    });
    return {
      accessToken: accessToken.value,
      expiresIn: accessToken.expiresInSeconds,
      refreshToken: opaqueToken.value,
      refreshExpiresAt: expiresAt,
      user: toUserReadModel(currentUser),
    };
  }
}
