import { SetMetadata } from '@nestjs/common';
import { PermissionCode } from '../../../domain/authorization/authorization';

/*
 * 路由权限装饰器固定为“全部满足”语义。
 * 未来任一权限场景必须新增独立装饰器，禁止布尔参数魔法。
 */
export const REQUIRED_PERMISSIONS_METADATA = Symbol('iam.required-permissions');

export const RequirePermissions = (...permissions: readonly PermissionCode[]) =>
  SetMetadata(REQUIRED_PERMISSIONS_METADATA, permissions);
