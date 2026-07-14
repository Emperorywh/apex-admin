import { Injectable } from '@nestjs/common';
import jwt, { JwtPayload } from 'jsonwebtoken';
import { RuntimeConfig } from '../../../../platform/config/runtime-config';
import {
  ALL_PERMISSIONS,
  isPermissionCode,
  isUserRole,
} from '../../domain/authorization/authorization';
import { IamError, IamErrors } from '../../application/errors/iam.error';
import { AuthenticatedActor } from '../../application/contracts/authenticated-actor';
import {
  AccessTokenService,
  SignAccessTokenInput,
  SignedAccessToken,
} from '../../application/ports/security.ports';

/*
 * JWT Adapter 固定 HS256、单密钥、无 kid，并严格验证全部授权 claims。
 * SUPER_ADMIN 也只能在完成同一验证流程后进入授权层 bypass。
 */
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

@Injectable()
export class Hs256AccessTokenService extends AccessTokenService {
  constructor(private readonly config: RuntimeConfig) {
    super();
  }

  sign(input: SignAccessTokenInput): SignedAccessToken {
    const issuedAtSeconds = Math.floor(input.now.getTime() / 1000);
    const expiresAt = new Date(
      (issuedAtSeconds + this.config.jwt.accessTtlSeconds) * 1000,
    );
    const value = jwt.sign(
      {
        sub: input.userId,
        sid: input.sessionId,
        role: input.role,
        perms: [...input.permissions],
        type: 'access',
        iat: issuedAtSeconds,
      },
      this.config.jwt.secret,
      {
        algorithm: 'HS256',
        issuer: this.config.jwt.issuer,
        audience: this.config.jwt.audience,
        expiresIn: this.config.jwt.accessTtlSeconds,
      },
    );
    return {
      value,
      expiresAt,
      expiresInSeconds: this.config.jwt.accessTtlSeconds,
    };
  }

  verify(token: string): AuthenticatedActor {
    try {
      const decoded = jwt.decode(token, { complete: true });
      if (!decoded || decoded.header.alg !== 'HS256' || decoded.header.kid !== undefined) {
        throw IamErrors.accessTokenInvalid();
      }
      const payload = jwt.verify(token, this.config.jwt.secret, {
        algorithms: ['HS256'],
        issuer: this.config.jwt.issuer,
        audience: this.config.jwt.audience,
      });
      if (typeof payload === 'string') throw IamErrors.accessTokenInvalid();
      return this.toActor(payload);
    } catch (error) {
      if (error instanceof IamError) throw error;
      throw IamErrors.accessTokenInvalid();
    }
  }

  private toActor(payload: JwtPayload): AuthenticatedActor {
    const claims = payload as Record<string, unknown>;
    const permissions = claims['perms'];
    const sessionId = claims['sid'];
    const role = claims['role'];
    if (
      claims['type'] !== 'access' ||
      typeof payload.sub !== 'string' ||
      !uuidPattern.test(payload.sub) ||
      typeof sessionId !== 'string' ||
      !uuidPattern.test(sessionId) ||
      !isUserRole(role) ||
      !Array.isArray(permissions) ||
      permissions.length > ALL_PERMISSIONS.length ||
      !permissions.every(isPermissionCode) ||
      new Set(permissions).size !== permissions.length ||
      typeof payload.iat !== 'number' ||
      typeof payload.exp !== 'number'
    ) {
      throw IamErrors.accessTokenInvalid();
    }
    return {
      id: payload.sub,
      sessionId,
      role,
      permissions,
      issuedAt: new Date(payload.iat * 1000),
      expiresAt: new Date(payload.exp * 1000),
    };
  }
}
