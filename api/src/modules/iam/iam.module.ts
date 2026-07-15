import { Module } from '@nestjs/common';
import { APP_FILTER, APP_GUARD } from '@nestjs/core';
import { RuntimeConfigModule } from '../../platform/config/runtime-config.module';
import { DatabaseClient } from '../../platform/database/database-client';
import { DatabaseModule } from '../../platform/database/database.module';
import { Clock, SystemClock } from '../../shared/kernel/clock';
import { IdGenerator, UuidV7Generator } from '../../shared/kernel/id-generator';
import { UserPolicy } from './application/policies/user.policy';
import { RefreshSessionPolicy } from './application/policies/refresh-session.policy';
import {
  IamUnitOfWork,
  UserRepository,
} from './application/ports/persistence.ports';
import {
  AccessTokenService,
  IamSessionConfig,
  LoginRateLimiter,
  OpaqueTokenService,
  PasswordBlocklist,
  PasswordHasher,
  PasswordHashingConfig,
} from './application/ports/security.ports';
import { BootstrapSuperAdminUseCase } from './application/use-cases/accounts/bootstrap-super-admin.use-case';
import { ChangeUserRoleUseCase } from './application/use-cases/accounts/change-user-role.use-case';
import { CreateUserUseCase } from './application/use-cases/accounts/create-user.use-case';
import { DisableUserUseCase } from './application/use-cases/accounts/disable-user.use-case';
import { EnableUserUseCase } from './application/use-cases/accounts/enable-user.use-case';
import { GetUserUseCase } from './application/use-cases/accounts/get-user.use-case';
import { ListUsersUseCase } from './application/use-cases/accounts/list-users.use-case';
import { GetCurrentUserUseCase } from './application/use-cases/sessions/get-current-user.use-case';
import { LoginUseCase } from './application/use-cases/sessions/login.use-case';
import { LogoutUseCase } from './application/use-cases/sessions/logout.use-case';
import { RefreshSessionUseCase } from './application/use-cases/sessions/refresh-session.use-case';
import { Argon2PasswordHasher } from './infrastructure/crypto/argon2-password-hasher';
import { LocalPasswordBlocklist } from './infrastructure/crypto/local-password-blocklist';
import { NodeOpaqueTokenService } from './infrastructure/crypto/opaque-token.service';
import { RuntimePasswordHashingConfig } from './infrastructure/crypto/runtime-password-hashing.config';
import { Hs256AccessTokenService } from './infrastructure/jwt/hs256-access-token.service';
import { PrismaIamUnitOfWork } from './infrastructure/persistence/prisma/prisma-iam.unit-of-work';
import { PrismaUserRepository } from './infrastructure/persistence/prisma/prisma-user.repository';
import { InMemoryLoginRateLimiter } from './infrastructure/ratelimit/in-memory-login-rate-limiter';
import { RuntimeIamSessionConfig } from './infrastructure/session/runtime-iam-session.config';
import { AuthController } from './presentation/http/auth.controller';
import { AccessTokenGuard } from './presentation/http/guards/access-token.guard';
import { PermissionsGuard } from './presentation/http/guards/permissions.guard';
import { IamProblemDetailsFilter } from './presentation/http/iam-problem-details.filter';
import { RefreshCookieFactory } from './presentation/http/refresh-cookie.factory';
import { UserIdPipe } from './presentation/http/user-id.pipe';
import { UserPageCursorCodec } from './presentation/http/user-page-cursor.codec';
import { UsersController } from './presentation/http/users.controller';

/*
 * IAM Module 只负责把 Application 端口绑定到 Infrastructure Adapter。
 * 账户、会话与授权保持同一一致性边界，内部仓储不会对外导出。
 */
const ACCOUNT_USE_CASES = [
  BootstrapSuperAdminUseCase,
  CreateUserUseCase,
  GetUserUseCase,
  ListUsersUseCase,
  ChangeUserRoleUseCase,
  DisableUserUseCase,
  EnableUserUseCase,
];

const SESSION_USE_CASES = [
  LoginUseCase,
  RefreshSessionUseCase,
  LogoutUseCase,
  GetCurrentUserUseCase,
];

@Module({
  imports: [RuntimeConfigModule, DatabaseModule],
  controllers: [AuthController, UsersController],
  providers: [
    ...ACCOUNT_USE_CASES,
    ...SESSION_USE_CASES,
    UserPolicy,
    RefreshSessionPolicy,
    RefreshCookieFactory,
    UserPageCursorCodec,
    UserIdPipe,
    { provide: Clock, useClass: SystemClock },
    { provide: IdGenerator, useClass: UuidV7Generator },
    { provide: PasswordHashingConfig, useClass: RuntimePasswordHashingConfig },
    { provide: PasswordHasher, useClass: Argon2PasswordHasher },
    { provide: PasswordBlocklist, useClass: LocalPasswordBlocklist },
    { provide: OpaqueTokenService, useClass: NodeOpaqueTokenService },
    { provide: AccessTokenService, useClass: Hs256AccessTokenService },
    { provide: LoginRateLimiter, useClass: InMemoryLoginRateLimiter },
    { provide: IamSessionConfig, useClass: RuntimeIamSessionConfig },
    { provide: IamUnitOfWork, useClass: PrismaIamUnitOfWork },
    {
      provide: UserRepository,
      inject: [DatabaseClient],
      useFactory: (database: DatabaseClient) => new PrismaUserRepository(database),
    },
    { provide: APP_GUARD, useClass: AccessTokenGuard },
    { provide: APP_GUARD, useClass: PermissionsGuard },
    { provide: APP_FILTER, useClass: IamProblemDetailsFilter },
  ],
})
export class IamModule {}
