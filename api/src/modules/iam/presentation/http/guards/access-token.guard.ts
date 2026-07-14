import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { IamErrors } from '../../../application/errors/iam.error';
import { AccessTokenService } from '../../../application/ports/security.ports';
import { PUBLIC_ROUTE_METADATA } from '../decorators/public.decorator';
import { IamRequestContext } from '../iam-request-context';

/*
 * AccessTokenGuard 只回答“是谁”：提取 Bearer、完整验证并注入 actor。
 * 它不查数据库，也不执行路由权限或对象级 Policy。
 */
@Injectable()
export class AccessTokenGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly accessTokens: AccessTokenService,
  ) {}

  canActivate(context: ExecutionContext): boolean {
    const isPublic = this.reflector.getAllAndOverride<boolean>(PUBLIC_ROUTE_METADATA, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const request = context.switchToHttp().getRequest<IamRequestContext>();
    const authorization = request.header('authorization');
    const match = authorization?.match(/^Bearer ([^\s]+)$/);
    if (!match?.[1]) throw IamErrors.accessTokenInvalid();
    request.actor = this.accessTokens.verify(match[1]);
    return true;
  }
}
