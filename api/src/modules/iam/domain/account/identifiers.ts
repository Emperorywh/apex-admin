import { DomainInvariantError } from '../errors/domain-invariant.error';

/*
 * IAM 标识值对象在恢复时也验证 UUIDv7，避免非法持久值进入领域。
 * 三种标识使用独立类型，阻止用例意外混用字符串 ID。
 */
const uuidV7Pattern = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

abstract class UuidV7Identifier {
  protected constructor(readonly value: string) {
    if (!uuidV7Pattern.test(value)) {
      throw new DomainInvariantError('UUID_V7_REQUIRED', '标识必须是 UUIDv7');
    }
  }
}

export class UserId extends UuidV7Identifier {
  static from(value: string): UserId {
    return new UserId(value);
  }
}

export class SessionId extends UuidV7Identifier {
  static from(value: string): SessionId {
    return new SessionId(value);
  }
}

export class RefreshTokenId extends UuidV7Identifier {
  static from(value: string): RefreshTokenId {
    return new RefreshTokenId(value);
  }
}
