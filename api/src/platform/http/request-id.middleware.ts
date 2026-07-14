import { randomUUID } from 'node:crypto';
import { Injectable, NestMiddleware } from '@nestjs/common';
import { NextFunction, Response } from 'express';
import { RequestContext } from './request-context';

/*
 * 请求 ID 在最外层建立并回写响应头，供业务审计和故障定位关联。
 * 客户端值仅在满足长度与字符白名单时复用，避免日志注入。
 */
const requestIdPattern = /^[A-Za-z0-9_-]{8,64}$/;

@Injectable()
export class RequestIdMiddleware implements NestMiddleware {
  use(request: RequestContext, response: Response, next: NextFunction): void {
    const candidate = request.header('x-request-id');
    request.traceId = candidate && requestIdPattern.test(candidate) ? candidate : randomUUID();
    response.setHeader('x-request-id', request.traceId);
    next();
  }
}
