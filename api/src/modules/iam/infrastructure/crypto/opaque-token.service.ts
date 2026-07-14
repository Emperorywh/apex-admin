import { createHash, randomBytes } from 'node:crypto';
import { Injectable } from '@nestjs/common';
import {
  OpaqueToken,
  OpaqueTokenService,
} from '../../application/ports/security.ports';

/*
 * Refresh Token 使用 32 字节密码学随机值与无填充 base64url 编码。
 * 持久化层只接收 SHA-256 小写十六进制摘要。
 */
@Injectable()
export class NodeOpaqueTokenService extends OpaqueTokenService {
  generate(): OpaqueToken {
    const value = randomBytes(32).toString('base64url');
    return { value, hash: this.hash(value) };
  }

  hash(value: string): string {
    return createHash('sha256').update(value, 'utf8').digest('hex');
  }
}
