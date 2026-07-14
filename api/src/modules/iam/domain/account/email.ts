import { DomainInvariantError } from '../errors/domain-invariant.error';

/*
 * Email 在进入 IAM 边界时立即规范化为唯一表示。
 * 数据库仅保存 canonical value，不维护会漂移的双字段。
 */
const emailPattern = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export class Email {
  private constructor(readonly value: string) {}

  static create(input: string): Email {
    const canonical = input.trim().toLowerCase();
    return Email.fromCanonical(canonical);
  }

  static restore(value: string): Email {
    if (value !== value.trim().toLowerCase()) {
      throw new DomainInvariantError('EMAIL_NOT_CANONICAL', '持久化邮箱不是规范值');
    }
    return Email.fromCanonical(value);
  }

  private static fromCanonical(value: string): Email {
    if (value.length === 0 || value.length > 320 || !emailPattern.test(value)) {
      throw new DomainInvariantError('EMAIL_INVALID', '邮箱格式无效');
    }
    return new Email(value);
  }
}
