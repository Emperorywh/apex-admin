import { Injectable, PipeTransform } from '@nestjs/common';
import { ValidationProblemError } from '../../../../platform/http/validation-problem.error';

/*
 * 路由 ID 明确限定为 UUIDv7，与 IAM 标识生成策略保持一致。
 * 非法参数统一进入字段级 Problem Details，而不是领域仓储查询。
 */
const uuidV7Pattern = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

@Injectable()
export class UserIdPipe implements PipeTransform<string, string> {
  transform(value: string): string {
    if (!uuidV7Pattern.test(value)) {
      throw new ValidationProblemError([{ path: 'id', code: 'isUuidV7' }]);
    }
    return value;
  }
}
