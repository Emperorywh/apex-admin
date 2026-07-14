import {
  Body,
  Controller,
  Get,
  HttpCode,
  HttpStatus,
  Post,
  Req,
  Res,
} from '@nestjs/common';
import type { Response } from 'express';
import type { AuthenticatedActor } from '../../application/contracts/authenticated-actor';
import { GetCurrentUserUseCase } from '../../application/use-cases/sessions/get-current-user.use-case';
import { LoginUseCase } from '../../application/use-cases/sessions/login.use-case';
import { LogoutUseCase } from '../../application/use-cases/sessions/logout.use-case';
import { RefreshSessionUseCase } from '../../application/use-cases/sessions/refresh-session.use-case';
import { CurrentActor } from './decorators/current-actor.decorator';
import { Public } from './decorators/public.decorator';
import { RequireTrustedOrigin } from './decorators/trusted-origin.decorator';
import { LoginRequestDto } from './dto/auth-request.dto';
import {
  REFRESH_COOKIE_NAME,
  RefreshCookieFactory,
} from './refresh-cookie.factory';
import { presentAuthUser } from './user.presenter';
import type { IamRequestContext } from './iam-request-context';

/*
 * Auth Controller 只转换 HTTP 请求、调用单一用例并处理 Cookie 协议副作用。
 * Session 状态机、令牌轮换与授权规则全部位于 Application/Domain。
 */
@Controller({ path: 'auth', version: '1' })
export class AuthController {
  constructor(
    private readonly login: LoginUseCase,
    private readonly refresh: RefreshSessionUseCase,
    private readonly logout: LogoutUseCase,
    private readonly currentUser: GetCurrentUserUseCase,
    private readonly cookies: RefreshCookieFactory,
  ) {}

  @Public()
  @RequireTrustedOrigin()
  @Post('login')
  @HttpCode(HttpStatus.OK)
  async loginUser(
    @Body() body: LoginRequestDto,
    @Req() request: IamRequestContext,
    @Res({ passthrough: true }) response: Response,
  ) {
    const result = await this.login.execute({
      email: body.email,
      password: body.password,
      ipAddress: request.ip ?? request.socket.remoteAddress ?? 'unknown',
      correlationId: request.traceId,
    });
    this.cookies.set(response, result.refreshToken, result.refreshExpiresAt);
    return {
      data: {
        accessToken: result.accessToken,
        tokenType: 'Bearer',
        expiresIn: result.expiresIn,
        user: presentAuthUser(result.user),
      },
    };
  }

  @Public()
  @RequireTrustedOrigin()
  @Post('refresh')
  @HttpCode(HttpStatus.OK)
  async refreshSession(
    @Req() request: IamRequestContext,
    @Res({ passthrough: true }) response: Response,
  ) {
    const result = await this.refresh.execute({
      refreshToken: this.readRefreshCookie(request),
      correlationId: request.traceId,
    });
    this.cookies.set(response, result.refreshToken, result.refreshExpiresAt);
    return {
      data: {
        accessToken: result.accessToken,
        tokenType: 'Bearer',
        expiresIn: result.expiresIn,
        user: presentAuthUser(result.user),
      },
    };
  }

  @Public()
  @RequireTrustedOrigin()
  @Post('logout')
  @HttpCode(HttpStatus.NO_CONTENT)
  async logoutSession(
    @Req() request: IamRequestContext,
    @Res({ passthrough: true }) response: Response,
  ): Promise<void> {
    try {
      await this.logout.execute({
        refreshToken: this.readRefreshCookie(request),
        correlationId: request.traceId,
      });
    } finally {
      this.cookies.clear(response);
    }
  }

  @Get('me')
  async me(@CurrentActor() actor: AuthenticatedActor) {
    const result = await this.currentUser.execute(actor);
    return {
      data: {
        user: presentAuthUser(result.user),
        authorization: {
          tokenRole: result.authorization.tokenRole,
          permissions: result.authorization.permissions,
          expiresAt: result.authorization.expiresAt.toISOString(),
          stale: result.authorization.stale,
        },
      },
    };
  }

  private readRefreshCookie(request: IamRequestContext): string | undefined {
    const cookies = (request as unknown as { readonly cookies?: unknown }).cookies;
    if (typeof cookies !== 'object' || cookies === null) return undefined;
    const value = (cookies as Record<string, unknown>)[REFRESH_COOKIE_NAME];
    return typeof value === 'string' ? value : undefined;
  }
}
