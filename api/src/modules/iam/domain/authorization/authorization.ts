import { UserRole } from '../account/user';

/*
 * 权限码、角色等级与权限快照共同构成授权领域的唯一事实来源。
 * 映射使用穷尽 Record，新增角色或权限时编译器会暴露遗漏。
 */
export const PermissionCode = {
  USER_READ: 'user:read',
  USER_CREATE: 'user:create',
  USER_STATUS_CHANGE: 'user:status:change',
  USER_ROLE_ASSIGN: 'user:role:assign',
} as const;

export type PermissionCode = (typeof PermissionCode)[keyof typeof PermissionCode];

export const ROLE_RANK: Readonly<Record<UserRole, number>> = Object.freeze({
  [UserRole.SUPER_ADMIN]: 400,
  [UserRole.ADMIN]: 300,
  [UserRole.OPERATOR]: 200,
  [UserRole.VIEWER]: 100,
});

export const ALL_PERMISSIONS: readonly PermissionCode[] = Object.freeze(
  Object.values(PermissionCode),
);

export const ROLE_PERMISSIONS: Readonly<Record<UserRole, readonly PermissionCode[]>> =
  Object.freeze({
    [UserRole.SUPER_ADMIN]: ALL_PERMISSIONS,
    [UserRole.ADMIN]: Object.freeze([
      PermissionCode.USER_READ,
      PermissionCode.USER_CREATE,
      PermissionCode.USER_STATUS_CHANGE,
      PermissionCode.USER_ROLE_ASSIGN,
    ]),
    [UserRole.OPERATOR]: Object.freeze([
      PermissionCode.USER_READ,
      PermissionCode.USER_CREATE,
    ]),
    [UserRole.VIEWER]: Object.freeze([PermissionCode.USER_READ]),
  });

export function isUserRole(value: unknown): value is UserRole {
  return typeof value === 'string' && Object.values(UserRole).includes(value as UserRole);
}

export function isPermissionCode(value: unknown): value is PermissionCode {
  return (
    typeof value === 'string' &&
    Object.values(PermissionCode).includes(value as PermissionCode)
  );
}
