import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { UserRole } from '../../../domain/account/user';
import { PermissionCode } from '../../../domain/authorization/authorization';
import { IamErrors } from '../../../application/errors/iam.error';
import { PUBLIC_ROUTE_METADATA } from '../decorators/public.decorator';
import { REQUIRED_PERMISSIONS_METADATA } from '../decorators/require-permissions.decorator';
import { IamRequestContext } from '../iam-request-context';

/*
 * PermissionsGuard 只判断 access 快照是否具备声明的全部路由能力。
 * SUPER_ADMIN bypass 仅在此授权层生效，不绕过 JWT 验证。
 */
@Injectable()
export class PermissionsGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const isPublic = this.reflector.getAllAndOverride<boolean>(PUBLIC_ROUTE_METADATA, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;
    const required = this.reflector.getAllAndOverride<readonly PermissionCode[]>(
      REQUIRED_PERMISSIONS_METADATA,
      [context.getHandler(), context.getClass()],
    );
    if (!required || required.length === 0) return true;

    const actor = context.switchToHttp().getRequest<IamRequestContext>().actor;
    if (!actor) throw IamErrors.accessTokenInvalid();
    if (actor.role === UserRole.SUPER_ADMIN) return true;
    if (!required.every((permission) => actor.permissions.includes(permission))) {
      throw IamErrors.insufficientPrivilege();
    }
    return true;
  }
}
