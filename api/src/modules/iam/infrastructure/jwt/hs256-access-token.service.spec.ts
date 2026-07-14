import jwt from 'jsonwebtoken';
import { loadRuntimeConfig } from '../../../../platform/config/runtime-config';
import { UserRole } from '../../domain/account/user';
import { ROLE_PERMISSIONS } from '../../domain/authorization/authorization';
import { Hs256AccessTokenService } from './hs256-access-token.service';

/*
 * JWT Adapter 测试覆盖固定算法、无 kid 与 claims 白名单。
 * 测试使用本地随机形态密钥，不依赖真实环境 Secret。
 */
describe('Hs256AccessTokenService', () => {
  const secret = Buffer.alloc(32, 7);
  const config = loadRuntimeConfig({
    NODE_ENV: 'test',
    DATABASE_URL: 'postgresql://test:test@localhost:5432/test',
    JWT_ACCESS_SECRET_BASE64: secret.toString('base64'),
    CORS_ORIGINS: 'https://admin.apex.local',
  });
  const service = new Hs256AccessTokenService(config);
  const userId = '019f5fdf-100f-7c10-9748-6dc673e0b1fd';
  const sessionId = '019f5fdf-100f-7c10-9748-6dc673e0b1fe';

  it('签发并验证完整 access 快照', () => {
    const now = new Date();
    const signed = service.sign({
      userId,
      sessionId,
      role: UserRole.ADMIN,
      permissions: ROLE_PERMISSIONS[UserRole.ADMIN],
      now,
    });
    const actor = service.verify(signed.value);
    expect(actor).toMatchObject({
      id: userId,
      sessionId,
      role: UserRole.ADMIN,
      permissions: ROLE_PERMISSIONS[UserRole.ADMIN],
    });
    expect(signed.expiresInSeconds).toBe(900);
  });

  it('拒绝带 kid 的 HS256 token', () => {
    const token = jwt.sign(
      {
        sub: userId,
        sid: sessionId,
        role: UserRole.VIEWER,
        perms: ROLE_PERMISSIONS[UserRole.VIEWER],
        type: 'access',
      },
      secret,
      {
        algorithm: 'HS256',
        issuer: 'apex-admin',
        audience: 'apex-admin-web',
        expiresIn: 900,
        keyid: 'unexpected-key',
      },
    );
    expect(() => service.verify(token)).toThrow('Access Token');
  });

  it('拒绝重复或未知权限码', () => {
    for (const permissions of [
      ['user:read', 'user:read'],
      ['user:unknown'],
    ]) {
      const token = jwt.sign(
        {
          sub: userId,
          sid: sessionId,
          role: UserRole.VIEWER,
          perms: permissions,
          type: 'access',
        },
        secret,
        {
          algorithm: 'HS256',
          issuer: 'apex-admin',
          audience: 'apex-admin-web',
          expiresIn: 900,
        },
      );
      expect(() => service.verify(token)).toThrow('Access Token');
    }
  });
});
