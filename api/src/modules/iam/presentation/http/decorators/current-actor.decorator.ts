import { createParamDecorator, ExecutionContext } from '@nestjs/common';
import { IamRequestContext } from '../iam-request-context';

/*
 * actor 只能由 AccessTokenGuard 注入，Controller 通过参数装饰器只读提取。
 * 装饰器不执行 JWT 验证，也不创建隐式授权状态。
 */
export const CurrentActor = createParamDecorator(
  (_data: unknown, context: ExecutionContext) =>
    context.switchToHttp().getRequest<IamRequestContext>().actor,
);
