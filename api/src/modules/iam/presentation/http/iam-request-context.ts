import { RequestContext } from '../../../../platform/http/request-context';
import type { AuthenticatedActor } from '../../application/contracts/authenticated-actor';

/*
 * IAM 请求扩展只在 Presentation 内为平台请求上下文增加 actor。
 * 平台 HTTP 层不反向依赖业务模块，actor 也不会进入跨请求单例状态。
 */
export interface IamRequestContext extends RequestContext {
  actor?: AuthenticatedActor;
}
