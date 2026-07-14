import { Request } from 'express';

/*
 * HTTP 请求上下文只保存当前请求的 traceId 与已验证 actor。
 * 两者由 Middleware/Guard 显式写入，请求结束后不会跨请求共享。
 */
export interface RequestContext extends Request {
  traceId: string;
}
