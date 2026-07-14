import { createHash, randomBytes } from 'node:crypto';

/*
 * UUIDv7 生成器把时间有序标识的技术细节封装在共享内核。
 * 用例只依赖 IdGenerator，测试可以注入确定性序列。
 */
export abstract class IdGenerator {
  abstract next(): string;
}

export class UuidV7Generator extends IdGenerator {
  next(): string {
    const bytes = Buffer.alloc(16);
    let timestamp = BigInt(Date.now());

    for (let index = 5; index >= 0; index -= 1) {
      bytes[index] = Number(timestamp & 0xffn);
      timestamp >>= 8n;
    }

    const random = randomBytes(10);
    bytes[6] = 0x70 | (random[0]! & 0x0f);
    bytes[7] = random[1]!;
    bytes[8] = 0x80 | (random[2]! & 0x3f);
    random.copy(bytes, 9, 3, 10);

    const hex = bytes.toString('hex');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
}

/*
 * 可重试事务使用事务外随机 namespace 与业务主键派生稳定 UUIDv8。
 * 同一次重试得到相同审计 ID，且不会在事务回调里消耗随机源。
 */
export function deriveUuidV8(namespace: string, subject: string): string {
  const bytes = createHash('sha256')
    .update(namespace, 'utf8')
    .update('\0', 'utf8')
    .update(subject, 'utf8')
    .digest()
    .subarray(0, 16);
  bytes[6] = 0x80 | (bytes[6]! & 0x0f);
  bytes[8] = 0x80 | (bytes[8]! & 0x3f);
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
