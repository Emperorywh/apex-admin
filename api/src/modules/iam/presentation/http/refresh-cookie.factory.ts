import { Injectable } from '@nestjs/common';
import { CookieOptions, Response } from 'express';
import { RuntimeConfig } from '../../../../platform/config/runtime-config';
import { Clock } from '../../../../shared/kernel/clock';

/*
 * Refresh Cookie 的设置与清除复用同一属性工厂。
 * Domain 始终为空形成 Host-only Cookie，Path 固定匹配版本化认证端点。
 */
export const REFRESH_COOKIE_NAME = 'refresh_token';

@Injectable()
export class RefreshCookieFactory {
  constructor(
    private readonly config: RuntimeConfig,
    private readonly clock: Clock,
  ) {}

  set(response: Response, token: string, expiresAt: Date): void {
    const remainingMilliseconds = Math.max(
      0,
      expiresAt.getTime() - this.clock.now().getTime(),
    );
    response.cookie(REFRESH_COOKIE_NAME, token, {
      ...this.baseOptions(),
      maxAge: remainingMilliseconds,
    });
  }

  clear(response: Response): void {
    response.clearCookie(REFRESH_COOKIE_NAME, this.baseOptions());
  }

  private baseOptions(): CookieOptions {
    return {
      httpOnly: true,
      secure: this.config.cookie.secure,
      sameSite: 'lax',
      path: '/v1/auth',
    };
  }
}
