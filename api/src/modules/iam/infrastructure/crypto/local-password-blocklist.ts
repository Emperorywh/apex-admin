import { Injectable } from '@nestjs/common';
import { PasswordBlocklist } from '../../application/ports/security.ports';

/*
 * 本地 blocklist 覆盖常见泄露密码与系统上下文词，不进行网络查询。
 * 比较仅用于策略判定，原始密码和邮箱不会被记录或缓存。
 */
const BLOCKED_PASSWORDS = new Set([
  'passwordpassword',
  'password123456',
  '123456789012345',
  'qwertyuiopasdfg',
  'letmeinletmein',
  'adminadminadmin',
  'apexadminapexadmin',
]);

@Injectable()
export class LocalPasswordBlocklist extends PasswordBlocklist {
  contains(password: string, email: string): boolean {
    const comparable = password.toLocaleLowerCase('en-US');
    const localPart = email.split('@')[0] ?? '';
    return (
      BLOCKED_PASSWORDS.has(comparable) ||
      comparable === email ||
      (localPart.length >= 6 && comparable === localPart)
    );
  }
}
