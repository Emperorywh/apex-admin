import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { RuntimeConfig } from '../../../../../platform/config/runtime-config';
import { IamErrors } from '../../../application/errors/iam.error';
import { RequestContext } from '../../../../../platform/http/request-context';
import { TRUSTED_ORIGIN_METADATA } from '../decorators/trusted-origin.decorator';

/*
 * TrustedOriginGuard 只保护 Cookie 认证边界的三个写端点。
 * Origin 必须存在并与启动时验证的白名单逐字匹配。
 */
@Injectable()
export class TrustedOriginGuard implements CanActivate {
  constructor(
    private readonly config: RuntimeConfig,
    private readonly reflector: Reflector,
  ) {}

  canActivate(context: ExecutionContext): boolean {
    const requiresTrustedOrigin = this.reflector.getAllAndOverride<boolean>(
      TRUSTED_ORIGIN_METADATA,
      [context.getHandler(), context.getClass()],
    );
    if (!requiresTrustedOrigin) return true;
    const request = context.switchToHttp().getRequest<RequestContext>();
    const origin = request.header('origin');
    if (!origin || !this.config.http.corsOrigins.includes(origin)) {
      throw IamErrors.untrustedOrigin();
    }
    return true;
  }
}
