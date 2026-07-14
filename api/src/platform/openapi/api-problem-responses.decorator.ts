import { applyDecorators } from '@nestjs/common';
import { ApiExtraModels, ApiResponse, getSchemaPath } from '@nestjs/swagger';
import { ProblemDetailsDto } from './problem-details.dto';

/*
 * 可公开的错误状态集中在平台 HTTP 协议层，业务 Controller 只选择自身可能产生的状态。
 * 500 是所有端点共同的兜底契约，由装饰器统一补充，避免接口声明遗漏。
 */
const PROBLEM_DESCRIPTIONS = {
  400: '请求参数或请求体校验失败',
  401: '身份凭证无效或账号不可用',
  403: '来源校验或权限校验失败',
  404: '请求的资源不存在',
  409: '资源状态或并发修改发生冲突',
  422: '请求符合语法，但违反业务安全策略',
  429: '请求超过服务允许的速率或并发限制',
  500: '服务端发生未处理异常',
  503: '服务依赖尚未就绪',
} as const;

export type ProblemResponseStatus = keyof typeof PROBLEM_DESCRIPTIONS;

export function ApiProblemResponses(
  ...statuses: readonly ProblemResponseStatus[]
): MethodDecorator & ClassDecorator {
  const uniqueStatuses = [...new Set<ProblemResponseStatus>([...statuses, 500])];

  return applyDecorators(
    ApiExtraModels(ProblemDetailsDto),
    ...uniqueStatuses.map((status) =>
      ApiResponse({
        status,
        description: PROBLEM_DESCRIPTIONS[status],
        content: {
          'application/problem+json': {
            schema: { $ref: getSchemaPath(ProblemDetailsDto) },
          },
        },
        ...(status === 429
          ? {
              headers: {
                'Retry-After': {
                  description: '客户端再次请求前需要等待的秒数',
                  schema: { type: 'integer', minimum: 1 },
                },
              },
            }
          : {}),
      }),
    ),
  );
}
