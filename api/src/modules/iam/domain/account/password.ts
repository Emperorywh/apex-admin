import { DomainInvariantError } from '../errors/domain-invariant.error';

/*
 * 密码建立时仅执行 Unicode NFC 与长度不变量，不做 trim 或大小写转换。
 * blocklist 属于 Application 策略，Argon2 编码属于 Infrastructure。
 */
export function normalizePassword(input: string): string {
  return input.normalize('NFC');
}

export class NewPassword {
  private constructor(readonly value: string) {}

  static create(input: string): NewPassword {
    const normalized = normalizePassword(input);
    const codePointLength = Array.from(normalized).length;
    if (codePointLength < 15 || codePointLength > 128) {
      throw new DomainInvariantError(
        'PASSWORD_LENGTH_INVALID',
        '密码长度必须为 15 到 128 个 Unicode code point',
      );
    }
    return new NewPassword(normalized);
  }
}

export class PasswordHash {
  private constructor(readonly value: string) {}

  static restore(value: string): PasswordHash {
    if (!value.startsWith('$argon2id$')) {
      throw new DomainInvariantError('PASSWORD_HASH_INVALID', '密码哈希必须使用 Argon2id');
    }
    return new PasswordHash(value);
  }

  static fromArgon2id(value: string): PasswordHash {
    return PasswordHash.restore(value);
  }
}
