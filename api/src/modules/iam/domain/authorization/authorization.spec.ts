import { UserRole } from '../account/user';
import {
  ALL_PERMISSIONS,
  isPermissionCode,
  PermissionCode,
  ROLE_PERMISSIONS,
  ROLE_RANK,
} from './authorization';

/*
 * 授权映射测试穷尽所有固定角色与权限码。
 * 新增角色或权限时必须同步修改矩阵，否则测试立即失败。
 */
describe('授权事实来源', () => {
  it('每个角色都有等级与具体权限快照', () => {
    for (const role of Object.values(UserRole)) {
      expect(ROLE_RANK[role]).toBeGreaterThan(0);
      expect(ROLE_PERMISSIONS[role].every(isPermissionCode)).toBe(true);
      expect(new Set(ROLE_PERMISSIONS[role]).size).toBe(ROLE_PERMISSIONS[role].length);
    }
  });

  it('SUPER_ADMIN 序列化全部具体权限', () => {
    expect(ROLE_PERMISSIONS[UserRole.SUPER_ADMIN]).toEqual(ALL_PERMISSIONS);
    expect(ALL_PERMISSIONS).toEqual(Object.values(PermissionCode));
  });
});
