import {
  CallHandler,
  ExecutionContext,
  Injectable,
  Logger,
  NestInterceptor,
} from '@nestjs/common';
import { Response } from 'express';
import { Observable } from 'rxjs';
import { tap } from 'rxjs/operators';
import { RequestContext } from '../http/request-context';

/*
 * 请求日志只记录结构化的非敏感元数据，不读取 query、body、Header 或 Cookie。
 * traceId 将运行日志与同事务安全审计关联，耗时使用单调时钟计算。
 */
@Injectable()
export class HttpRequestLoggingInterceptor implements NestInterceptor {
  private readonly logger = new Logger('HttpRequest');

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const http = context.switchToHttp();
    const request = http.getRequest<RequestContext>();
    const response = http.getResponse<Response>();
    const startedAt = process.hrtime.bigint();

    return next.handle().pipe(
      tap({
        complete: () => this.writeLog(request, startedAt, 'success', response.statusCode),
        error: () => this.writeLog(request, startedAt, 'error'),
      }),
    );
  }

  private writeLog(
    request: RequestContext,
    startedAt: bigint,
    outcome: 'success' | 'error',
    statusCode?: number,
  ): void {
    const durationMilliseconds = Number(process.hrtime.bigint() - startedAt) / 1_000_000;
    this.logger.log(
      JSON.stringify({
        event: 'http_request_completed',
        traceId: request.traceId,
        method: request.method,
        path: request.path,
        outcome,
        ...(statusCode === undefined ? {} : { statusCode }),
        durationMilliseconds: Number(durationMilliseconds.toFixed(3)),
      }),
    );
  }
}
