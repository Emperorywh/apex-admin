import {
  ArgumentsHost,
  Catch,
  ExceptionFilter,
  HttpException,
  Injectable,
  Logger,
} from '@nestjs/common';
import { Response } from 'express';
import { RequestContext } from '../../../../platform/http/request-context';
import { ValidationProblemError } from '../../../../platform/http/validation-problem.error';
import { redactSensitiveText } from '../../../../platform/logging/redact-sensitive-text';
import { IamError, IamErrorCode } from '../../application/errors/iam.error';

/*
 * 全局 Filter 将已知业务/校验错误映射为 RFC 9457 Problem Details。
 * 未知异常只记录服务端诊断信息，对客户端固定返回通用 500。
 */
const IAM_PROBLEMS: Readonly<Record<IamErrorCode, { status: number; title: string }>> = {
  ACCESS_TOKEN_INVALID: { status: 401, title: 'Access Token 无效' },
  LOGIN_ACCOUNT_NOT_FOUND: { status: 401, title: '登录账号不存在' },
  INVALID_CREDENTIALS: { status: 401, title: '密码错误' },
  USER_DISABLED: { status: 401, title: '用户已禁用' },
  REFRESH_TOKEN_INVALID: { status: 401, title: 'Refresh Token 无效' },
  REFRESH_TOKEN_REPLAY: { status: 401, title: '检测到 Refresh Token 重放' },
  REFRESH_TOKEN_STALE: { status: 409, title: 'Refresh Token 已陈旧' },
  UNTRUSTED_ORIGIN: { status: 403, title: 'Origin 不可信' },
  INSUFFICIENT_PRIVILEGE: { status: 403, title: '权限不足' },
  USER_NOT_FOUND: { status: 404, title: '用户不存在' },
  USER_EMAIL_ALREADY_USED: { status: 409, title: '邮箱已被使用' },
  PASSWORD_NOT_ALLOWED: { status: 422, title: '密码不符合安全策略' },
  LAST_SUPER_ADMIN: { status: 409, title: '不能移除最后一个活跃超级管理员' },
  CONCURRENT_MODIFICATION: { status: 409, title: '并发修改冲突' },
  RATE_LIMIT_EXCEEDED: { status: 429, title: '请求过于频繁' },
};

@Catch()
@Injectable()
export class IamProblemDetailsFilter implements ExceptionFilter {
  private readonly logger = new Logger(IamProblemDetailsFilter.name);

  catch(exception: unknown, host: ArgumentsHost): void {
    const http = host.switchToHttp();
    const response = http.getResponse<Response>();
    const request = http.getRequest<RequestContext>();

    if (exception instanceof ValidationProblemError) {
      response
        .status(400)
        .type('application/problem+json')
        .send({
          type: 'https://apex.example.com/problems/validation-failed',
          title: '请求字段验证失败',
          status: 400,
          code: 'VALIDATION_FAILED',
          traceId: request.traceId,
          errors: exception.errors,
        });
      return;
    }

    if (exception instanceof IamError) {
      const problem = IAM_PROBLEMS[exception.code];
      if (exception.retryAfterSeconds !== undefined) {
        response.setHeader('Retry-After', exception.retryAfterSeconds.toString());
      }
      response
        .status(problem.status)
        .type('application/problem+json')
        .send({
          type: `https://apex.example.com/problems/${exception.code.toLowerCase().replaceAll('_', '-')}`,
          title: problem.title,
          status: problem.status,
          code: exception.code,
          traceId: request.traceId,
        });
      return;
    }

    if (exception instanceof HttpException && exception.getStatus() === 400) {
      response.status(400).type('application/problem+json').send({
        type: 'https://apex.example.com/problems/validation-failed',
        title: '请求格式无效',
        status: 400,
        code: 'VALIDATION_FAILED',
        traceId: request.traceId,
      });
      return;
    }

    if (exception instanceof HttpException && exception.getStatus() === 503) {
      response.status(503).type('application/problem+json').send({
        type: 'https://apex.example.com/problems/internal-server-error',
        title: '服务依赖尚未就绪',
        status: 503,
        code: 'INTERNAL_SERVER_ERROR',
        traceId: request.traceId,
      });
      return;
    }

    this.logger.error(
      '未处理的服务端异常',
      redactSensitiveText(exception instanceof Error ? exception.stack : undefined),
    );
    response
      .status(500)
      .type('application/problem+json')
      .send({
        type: 'https://apex.example.com/problems/internal-server-error',
        title: '服务端内部错误',
        status: 500,
        code: 'INTERNAL_SERVER_ERROR',
        traceId: request.traceId,
      });
  }
}
