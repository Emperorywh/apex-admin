import { Injectable } from '@nestjs/common';
import { z } from 'zod';
import { UserPageCursor } from '../../application/contracts/read-models';
import { ValidationProblemError } from '../../../../platform/http/validation-problem.error';

/*
 * Cursor 使用版本化 JSON 的无填充 base64url 表示，并在解码后严格验证。
 * 未验证字符串永远不会作为 Prisma 参数进入 Infrastructure。
 */
const cursorSchema = z.object({
  v: z.literal(1),
  createdAt: z.iso.datetime({ offset: true }),
  id: z.string().regex(
    /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  ),
});

@Injectable()
export class UserPageCursorCodec {
  encode(cursor: UserPageCursor): string {
    return Buffer.from(
      JSON.stringify({ v: 1, createdAt: cursor.createdAt.toISOString(), id: cursor.id }),
      'utf8',
    ).toString('base64url');
  }

  decode(value: string | undefined): UserPageCursor | null {
    if (!value) return null;
    if (!/^[A-Za-z0-9_-]+$/.test(value)) this.fail();
    try {
      const decoded = Buffer.from(value, 'base64url').toString('utf8');
      const canonical = Buffer.from(decoded, 'utf8').toString('base64url');
      if (canonical !== value) this.fail();
      const parsed = cursorSchema.parse(JSON.parse(decoded));
      return { createdAt: new Date(parsed.createdAt), id: parsed.id };
    } catch {
      return this.fail();
    }
  }

  private fail(): never {
    throw new ValidationProblemError([{ path: 'cursor', code: 'invalidCursor' }]);
  }
}
